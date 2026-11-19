// An address that names no screen (#/cabnet, an old or mistyped link): it says so and offers the ways back, as the site's 404 page does.
export function MissingPage({ path }: { path: string }) {
  return (
    <div class="screen missing-screen" data-testid="missing">
      <div class="missing">
        <div class="missing-k">Nothing at #/{path}</div>
        <h1>This page is not in the sky</h1>
        <p>The address may be mistyped, or the page has moved. The sky, the lab and the findings are a click away.</p>
        <nav class="missing-go" aria-label="Ways back">
          <a class="btn primary" href="#/">
            Home
          </a>
          <a class="btn" href="#/explore">
            The globe
          </a>
          <a class="btn" href="#/findings">
            Findings
          </a>
          <a class="btn" href="#/me">
            Cabinet
          </a>
        </nav>
      </div>
    </div>
  );
}
