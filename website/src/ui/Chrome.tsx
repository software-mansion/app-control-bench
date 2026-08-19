// Page chrome shared by the report and the explorer: the header nav, the section header with its
// optional [i] explanation, and the site footer. Ports nav_bar (runner/report.py:660) and sec (648).
import { Fragment } from "preact";

import type { Platform, Provenance } from "../shared/contract";
import { GithubMark } from "./GithubMark";
import swmMark from "../assets/swm-mark-outline-left-top.svg";

export const REPO_URL =
  "https://github.com/software-mansion-labs/app-control-bench";

/**
 * The two pages are separate files, so the current one is marked rather than routed to. Same
 * markup on both — explorer has no dark hero behind it, so `site-nav--inverted` flips the color
 * tokens (cream-on-dark -> ink-on-light) without touching sizing/layout.
 */
export function Nav({
  page,
  platform,
}: {
  page: "report" | "explorer";
  platform: Platform;
}) {
  const reportHref = platform.id === "ios" ? "/" : "/android.html";
  const explorerHref = platform.id === "ios" ? "/index-runs.html" : "/android-runs.html";
  const platformHref = (id: Platform["id"]) => {
    if (page === "explorer") {
      return id === "ios" ? "/index-runs.html" : "/android-runs.html";
    }
    return id === "ios" ? "/" : "/android.html";
  };
  return (
    <header
      class={
        page === "report"
          ? "site-nav site-nav--benchmark"
          : "site-nav site-nav--benchmark site-nav--inverted"
      }
    >
      <div class="nav-brand-group">
        <a
          class="nav-swm-mark"
          href="https://swmansion.com/"
          target="_blank"
          rel="noopener"
          aria-label="Software Mansion"
        >
          <img src={swmMark} alt="Software Mansion" aria-hidden="true" />
        </a>
      </div>
      <div class="nav-links">
        <nav class="platform-switch" aria-label="Benchmark platform">
          {(["ios", "android"] as const).map((id) => (
            <a
              key={id}
              href={platformHref(id)}
              class={platform.id === id ? "is-active" : undefined}
              aria-current={platform.id === id ? "page" : undefined}
            >
              {id === "ios" ? "iOS" : "Android"}
            </a>
          ))}
        </nav>
        <a
          href={reportHref}
          class={page === "report" ? "nav-link is-active" : "nav-link"}
          aria-current={page === "report" ? "page" : undefined}
        >
          Report
        </a>
        <a
          href={explorerHref}
          class={page === "explorer" ? "nav-link is-active" : "nav-link"}
          aria-current={page === "explorer" ? "page" : undefined}
        >
          Run explorer
        </a>
        <a
          href={REPO_URL}
          class="nav-link nav-github"
          target="_blank"
          rel="noopener"
          aria-label="GitHub repository"
        >
          <GithubMark />
        </a>
      </div>
    </header>
  );
}

/**
 * A section header. With `explain`, the methodology caption is hidden and offered as an [i] tooltip;
 * the .sub.explain copy is revealed only under [data-tw-explain="on"], which the shell does not set —
 * that absence is what keeps the [i] affordance rather than inline captions.
 */
export function SectionHeader({
  title,
  explain,
}: {
  title: string;
  explain?: string;
}) {
  return (
    <div class="sec" >
      <header>
        <h2>{title}</h2>
      </header>
      {explain ? <p class="chart-description">{explain}</p> : null}
    </div>
  );
}

/** "bluesky v1.122.0 (7f5dd40)", the commit linking to GitHub. report.py:2081. */
function AppVersionBit({
  app,
  info,
}: {
  app: string;
  info: { version?: string; commit?: string; repo?: string };
}) {
  const commit = info.commit ? (
    <>
      {" ("}
      {info.repo ? (
        <a
          href={`https://github.com/${info.repo}/commit/${info.commit}`}
          target="_blank"
          rel="noopener"
        >
          {info.commit.slice(0, 7)}
        </a>
      ) : (
        info.commit.slice(0, 7)
      )}
      {")"}
    </>
  ) : null;
  return (
    <>
      {app}
      {info.version ? ` v${info.version}` : ""}
      {commit}
    </>
  );
}

export function ReportFooter({ provenance }: { provenance: Provenance }) {
  const releases = Object.entries(provenance.releaseToolVersions).map(
    ([tool, version]) => `${tool} v${version}`,
  );
  const tools = Object.entries(provenance.toolVersions).map(
    ([tool, version]) => `${tool} v${version}`,
  );
  const apps = Object.entries(provenance.appVersions).filter(
    ([, info]) => info.version || info.commit,
  );
  return (
    <footer class="site-footer">
      <div class="provenance">
        <span>Generated {provenance.generatedAt}</span>
        <span>{provenance.judgeLine}</span>
        <span>{provenance.resultPolicy}</span>
        {releases.length > 0 && <span>Benchmark release {releases.join(" · ")}</span>}
        {tools.length > 0 && <span>Recorded runs {tools.join(" · ")}</span>}
        {provenance.costNotice && (
          <span>
            <a href={provenance.costNotice.href} target="_blank" rel="noopener">
              {provenance.costNotice.label}
            </a>
          </span>
        )}
        {apps.length > 0 && (
          <span>
            {apps.map(([app, info], i) => (
              <Fragment key={app}>
                {i > 0 ? ", " : ""}
                <AppVersionBit app={app} info={info} />
              </Fragment>
            ))}
          </span>
        )}
      </div>
    </footer>
  );
}
