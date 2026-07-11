// Placeholder Home/Status route (issue #93). The real content — dependency
// cards, recent-blocks table, config summary — is issue #96's scope; this
// slice only proves the shell routes here and renders something.
export function Home() {
  return (
    <div className="bf-card">
      <h1>Home</h1>
      <p>Status content lands in a follow-up slice (issue #96).</p>
    </div>
  );
}
