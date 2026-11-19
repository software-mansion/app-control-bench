// Page chrome shared by the report and the explorer: the header nav, the section header with its
// optional [i] explanation, and the site footer. Ports nav_bar (runner/report.py:537) and sec (525).
import { Fragment } from "preact";

import type { BuildManifest, Provenance } from "../shared/contract";
import { pageHref } from "../shared/editions";
import { GithubMark } from "./GithubMark";
import swmMark from "../assets/swm-mark-outline-left-top.svg";

export const REPO_URL =
  "https://github.com/software-mansion-labs/app-control-bench";

/**
 * The pages are separate files, so the current one is marked rather than routed to. Same markup on
 * all of them - only the report has a dark hero behind it, so `site-nav--inverted` flips the color
 * tokens (cream-on-dark -> ink-on-light) elsewhere without touching sizing/layout.
 */
export function Nav({
  page,
  manifest,
}: {
  page: "report" | "explorer" | "telemetry";
  /** Carries the edition, so the links stay inside the edition the reader is looking at. */
  manifest: BuildManifest;
}) {
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
          href="https://swmansion.com/"
          target="_blank"
          rel="noopener"
          aria-label="Software Mansion"
        >
          <img
            class="nav-swm-mark"
            src={swmMark}
            alt="Software Mansion"
            aria-hidden="true"
          />
        </a>
      </div>
      <div class="nav-links">
        <a
          href={pageHref(manifest, "report")}
          class={page === "report" ? "nav-link is-active" : "nav-link"}
          aria-current={page === "report" ? "page" : undefined}
        >
          Report
        </a>
        <a
          href={pageHref(manifest, "explorer")}
          class={page === "explorer" ? "nav-link is-active" : "nav-link"}
          aria-current={page === "explorer" ? "page" : undefined}
        >
          Run explorer
        </a>
        {/* Telemetry is internal: publish.yaml drops web/index-telemetry.html from the public
            environment, so the link must not appear on a site that has no such page. __HAS_TELEMETRY__
            is a build-time literal off that same file, so the public bundle drops this branch. */}
        {__HAS_TELEMETRY__ && (
          <a
            href={pageHref(manifest, "telemetry")}
            class={page === "telemetry" ? "nav-link is-active" : "nav-link"}
            aria-current={page === "telemetry" ? "page" : undefined}
          >
            Telemetry
          </a>
        )}
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
 * A section header. With `explain`, the methodology caption collapses into an [i] beside the title
 * that opens it on hover or focus. The same copy also goes out as .sub.explain, which report.css
 * prints inline below 768px, where nothing hovers.
 */
export function SectionHeader({
  title,
  explain,
}: {
  title: string;
  explain?: string;
}) {
  // Slugged from the title rather than generated: prerender and hydration have to agree on it.
  const tipId = `explain-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div class="sec">
      <header>
        <h2>{title}</h2>
        {explain ? (
          <span class="ehint">
            <button
              type="button"
              class="eii"
              aria-label={`What ${title} shows`}
              aria-describedby={tipId}
            >
              i
            </button>
            <span class="etip" role="tooltip" id={tipId}>
              {explain}
            </span>
          </span>
        ) : null}
      </header>
      {explain ? <div class="sub explain">{explain}</div> : null}
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
  // Every version of a toolkit the export carries, not just the one the page opens on: the footer is
  // provenance, so it states what the data covers rather than what is currently on screen.
  const tools = Object.entries(provenance.toolVersions).map(
    ([tool, versions]) => `${tool} ${versions.map((version) => `v${version}`).join(' / ')}`,
  );
  const apps = Object.entries(provenance.appVersions).filter(
    ([, info]) => info.version || info.commit,
  );
  return (
    <footer class="site-footer">
      <div class="provenance">
        <span>Generated {provenance.generatedAt}</span>
        {provenance.editionLine && <span>{provenance.editionLine}</span>}
        <span>{provenance.judgeLine}</span>
        {tools.length > 0 && <span>{tools.join(" · ")}</span>}
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
        <span>
          Read about our{" "}
          <a
            class="footer-privacy"
            href="https://swmansion.com/privacy/policy"
            target="_blank"
            rel="noopener"
          >
            privacy policy
          </a>
        </span>
      </div>
    </footer>
  );
}
