// Page chrome shared by the report and the explorer: the header nav, the section header with its
// optional [i] explanation, and the site footer. Ports nav_bar (runner/report.py:660) and sec (648).
import type { Provenance } from "../shared/contract";
import { GithubMark } from "./GithubMark";
import swmMark from "../assets/swm-mark-outline-left-top.svg";

export const REPO_URL =
  "https://github.com/software-mansion-labs/app-control-bench";

/**
 * The two pages are separate files, so the current one is marked rather than routed to. Same
 * markup on both — explorer has no dark hero behind it, so `site-nav--inverted` flips the color
 * tokens (cream-on-dark -> ink-on-light) without touching sizing/layout.
 */
export function Nav({ page }: { page: "report" | "explorer" }) {
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
        <a class="benchmark-brand" href="/" aria-label="AppControlBench home">
          <span>AppControlBench</span>
        </a>
      </div>
      <div class="nav-links">
        <a href="/" class="nav-link">
          Report
        </a>
        <a href="/index-runs" class="nav-link">
          Run explorer
        </a>
        <a
          href="https://github.com/software-mansion-labs/app-control-bench-prerelease"
          class="nav-link nav-github"
          target="_blank"
          rel="noopener"
          aria-label="GitHub repository"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 15 15"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M7.49933 0.25C3.49635 0.25 0.25 3.49593 0.25 7.50024C0.25 10.703 2.32715 13.4206 5.2081 14.3797C5.57084 14.446 5.70302 14.2222 5.70302 14.0299C5.70302 13.8576 5.69679 13.4019 5.69323 12.797C3.67661 13.235 3.25112 11.825 3.25112 11.825C2.92132 10.9874 2.44599 10.7644 2.44599 10.7644C1.78773 10.3149 2.49584 10.3238 2.49584 10.3238C3.22353 10.375 3.60629 11.0711 3.60629 11.0711C4.25298 12.1788 5.30335 11.8588 5.71638 11.6732C5.78225 11.205 5.96962 10.8854 6.17658 10.7043C4.56675 10.5209 2.87415 9.89918 2.87415 7.12104C2.87415 6.32925 3.15677 5.68257 3.62053 5.17563C3.54576 4.99226 3.29697 4.25521 3.69174 3.25691C3.69174 3.25691 4.30015 3.06196 5.68522 3.99973C6.26337 3.83906 6.8838 3.75895 7.50022 3.75583C8.1162 3.75895 8.73619 3.83906 9.31523 3.99973C10.6994 3.06196 11.3069 3.25691 11.3069 3.25691C11.7026 4.25521 11.4538 4.99226 11.3795 5.17563C11.8441 5.68257 12.1245 6.32925 12.1245 7.12104C12.1245 9.9063 10.4292 10.5192 8.81452 10.6985C9.07444 10.9224 9.30633 11.3648 9.30633 12.0413C9.30633 13.0102 9.29742 13.7922 9.29742 14.0299C9.29742 14.2239 9.42828 14.4496 9.79591 14.3788C12.6746 13.4179 14.75 10.7025 14.75 7.50024C14.75 3.49593 11.5036 0.25 7.49933 0.25Z"
              fill="currentColor"
              fill-rule="evenodd"
              clip-rule="evenodd"
            />
          </svg>
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
        {tools.length > 0 && <span>{tools.join(" · ")}</span>}
        {apps.length > 0 && (
          <span>
            {apps.map(([app, info], i) => (
              <>
                {i > 0 ? ", " : ""}
                <AppVersionBit app={app} info={info} />
              </>
            ))}
          </span>
        )}
      </div>
    </footer>
  );
}

export function ExplorerFooter() {
  return (
    <footer class="site-footer">
      <span>AppControlBench</span>
      <a class="ghl" href={REPO_URL} target="_blank" rel="noopener">
        <GithubMark />
        Source and data
      </a>
    </footer>
  );
}
