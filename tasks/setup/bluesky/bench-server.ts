/**
 * Persistent self-hosted atproto backend for the Bluesky benchmark (the atproto analogue of the
 * frozen self-hosted Synapse used for Element).
 *
 * Unlike dev-env's mock-server.ts (which CLOSES + RECREATES the whole TestNetwork on every POST,
 * rotating account DIDs), this creates the network ONCE, seeds a deterministic cat/dog world through
 * the Mocker (so the AppView indexes it via processAll), then stays up and exposes a tiny control API:
 *
 *   GET  /info    -> { pdsUrl, appviewDid, bench:{handle,password,did}, accounts:{...} }
 *   POST /reset   -> per-run reset: wipe ONLY bench's mutations (posts/likes/reposts/replies/extra
 *                    follows) + unmute all, then restore bench's seed follows + processAll. Leaves the
 *                    static content accounts + their posts untouched, and never recreates the network,
 *                    so account DIDs (and the golden's login session) stay valid across every run.
 *
 * Ports (see test-pds.ts): PDS :3000, PLC :3001, AppView on the next free port (:3002), control API on
 * :1987. test-pds.ts gives the AppView `publicUrl: http://localhost:2584` (the app's DEV_ENV_APPVIEW and
 * its DID document), so every image and avatar URL it hands out points at :2584 - where nothing listens.
 * forwardAppviewPublicPort() bridges :2584 to the AppView's real port; without it every feed image and
 * avatar is a grey placeholder and the image lightbox spins forever (task bsky-18).
 *
 * Run from dev-env/ (needs its node_modules) on the checkout's Node 24, as an ES module like the stock
 * mock-server.ts:  NODE_ENV=development ./dev-infra/with-test-redis-and-db.sh node ./bench-server.ts
 * (the wrapper starts the postgres+redis the AppView needs).
 */
import {createServer as createHTTPServer} from 'node:http'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'

import {createServer, type TestPDS} from './test-pds.ts'

const ASSETS = path.join(import.meta.dirname, 'bench-assets')
const PASSWORD = 'hunter2'

// content account -> ordered [imageFile, caption, alt]; index 0 is newest (first in the feed).
const CONTENT: Record<string, Array<[string, string, string]>> = {
  'whiskers.test': [
    ['cat1.jpg', 'Mochi napping in a sunbeam 🐱 #caturday', 'A kitten asleep in sunlight.'],
    ['cat2.jpg', 'someone is very much awake at 5am 🐈', 'A cat staring at the camera.'],
  ],
  'mittens.test': [['cat3.jpg', 'windowsill supervisor reporting for duty 😺', 'A tabby on a windowsill.']],
  'rex.test': [
    ['dog2.jpg', 'beach day! best day 🐶 #dogsofbluesky', 'A happy dog on a beach.'],
    ['dog1.jpg', 'he found the one muddy puddle in the whole park', 'A muddy dog.'],
  ],
  'buddy.test': [['dog3.jpg', 'good boy waiting patiently for a treat 🐕', 'A dog sitting and waiting.']],
}
const BENCH = 'bench.test'
const FOLLOWS = ['whiskers.test', 'rex.test', 'mittens.test', 'buddy.test'] // bench follows -> Following feed
const EXTRA = ['newfriend.test', 'loudspammer.test'] // exist, NOT followed (follow/mute task targets)

let server: TestPDS

async function agentPost(agent: any, img: string, text: string, alt: string, createdAt: string) {
  const bytes = fs.readFileSync(path.join(ASSETS, img))
  const up = await agent.uploadBlob(bytes, {encoding: 'image/jpeg'})
  await agent.post({
    text,
    createdAt,
    langs: ['en'],
    embed: {$type: 'app.bsky.embed.images', images: [{alt, image: up.data.blob}]},
  })
}

async function confirmBenchEmail() {
  // Mark bench.test's email verified in the PDS so the app never shows the "verify your email"
  // modal (which otherwise reappears mid-task on reply/compose and would break a run). createEmailToken
  // mints a confirm token; confirmEmail validates it and sets emailConfirmedAt. No mailer needed.
  const am: any = server.mocker.testNet.pds.ctx.accountManager
  const benchDid = server.mocker.users[BENCH.replace('.test', '')].did
  const token = await am.createEmailToken(benchDid, 'confirm_email')
  // The PDS that @atproto/dev-env ^0.5 resolves to now takes (did, email, token); older ones took {did, token}.
  if (am.confirmEmail.length >= 3) {
    const account = await am.getAccount(benchDid)
    await am.confirmEmail(benchDid, account.email, token)
  } else {
    await am.confirmEmail({did: benchDid, token})
  }
}

async function seedStatic() {
  // create every account (bench + content + extra)
  for (const h of [BENCH, ...Object.keys(CONTENT), ...EXTRA]) {
    await server.mocker.createUser(h.replace('.test', '')) // Mocker appends .test
  }
  try {
    await confirmBenchEmail()
    console.log('bench email confirmed')
  } catch (e) {
    console.error('confirmBenchEmail FAILED (non-fatal, will retry path):', String(e))
  }
  // content accounts post their animal images (staggered createdAt -> deterministic global order)
  let slot = Date.now()
  for (const [h, posts] of Object.entries(CONTENT)) {
    const u = server.mocker.users[h.replace('.test', '')]
    for (const [img, text, alt] of posts) {
      await agentPost(u.agent, img, text, alt, new Date(slot).toISOString())
      slot -= 180000 // 3 min older each
    }
  }
  await restoreBenchFollows()
  await server.mocker.testNet.processAll()
}

async function restoreBenchFollows() {
  for (const h of FOLLOWS) await server.mocker.follow(BENCH.replace('.test', ''), h.replace('.test', ''))
}

async function wipeBench() {
  const u = server.mocker.users[BENCH.replace('.test', '')]
  const did = u.did
  const agent = u.agent
  for (const coll of [
    'app.bsky.feed.post',
    'app.bsky.feed.like',
    'app.bsky.feed.repost',
    'app.bsky.graph.follow',
    'app.bsky.graph.block',
  ]) {
    let cursor: string | undefined
    do {
      const res = await agent.api.com.atproto.repo.listRecords({repo: did, collection: coll, limit: 100, cursor})
      for (const rec of res.data.records) {
        const rkey = rec.uri.split('/').pop() as string
        await agent.api.com.atproto.repo.deleteRecord({repo: did, collection: coll, rkey})
      }
      cursor = res.data.cursor
    } while (cursor)
  }
  // lift any mutes (mutes are AppView state, not repo records)
  try {
    const m = await agent.api.app.bsky.graph.getMutes({limit: 100})
    for (const a of m.data.mutes) await agent.api.app.bsky.graph.unmuteActor({actor: a.did})
  } catch {}
}

const APPVIEW_PUBLIC_PORT = 2584

function forwardAppviewPublicPort() {
  // See the header: the AppView's URLs say :2584, the AppView listens elsewhere. A plain TCP bridge, so
  // nothing about the network (DIDs, the golden's session) changes.
  const port = Number(new URL(server.mocker.testNet.bsky.url).port)
  if (port === APPVIEW_PUBLIC_PORT) return
  net
    .createServer(client => {
      const upstream = net.connect(port, '127.0.0.1')
      client.pipe(upstream).pipe(client)
      client.on('error', () => upstream.destroy())
      upstream.on('error', () => client.destroy())
    })
    .listen(APPVIEW_PUBLIC_PORT, () => console.log(`appview :${APPVIEW_PUBLIC_PORT} -> :${port} (image URLs)`))
}

async function main() {
  server = await createServer({inviteRequired: false})
  console.log('network up:', server.pdsUrl, 'appview', server.appviewDid)
  forwardAppviewPublicPort()
  await seedStatic()
  console.log('seeded. bench did =', server.mocker.users[BENCH.replace('.test', '')].did)

  createHTTPServer(async (req, res) => {
    try {
      if (req.method === 'GET' && req.url?.startsWith('/info')) {
        const accounts: Record<string, string> = {}
        for (const [name, u] of Object.entries(server.mocker.users)) accounts[name + '.test'] = (u as any).did
        return res.writeHead(200, {'content-type': 'application/json'}).end(
          JSON.stringify({
            pdsUrl: server.pdsUrl,
            appviewDid: server.appviewDid,
            bench: {handle: BENCH, password: PASSWORD, did: server.mocker.users['bench'].did},
            accounts,
          }),
        )
      }
      if (req.method === 'POST' && req.url?.startsWith('/reset')) {
        await wipeBench()
        await restoreBenchFollows()
        await server.mocker.testNet.processAll()
        return res.writeHead(200, {'content-type': 'application/json'}).end(JSON.stringify({reset: true}))
      }
      return res.writeHead(404).end()
    } catch (e) {
      console.error('control error', e)
      return res.writeHead(500).end(String(e))
    }
  }).listen(1987)
  console.log('bench control API on :1987 (GET /info, POST /reset)')
}

main().catch(e => {
  console.error('fatal', e)
  process.exit(1)
})
