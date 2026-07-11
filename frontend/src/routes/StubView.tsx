// Empty stub for a sidebar destination not yet migrated into the shell
// (issue #93 scope: shell + routing only). Each destination's real view
// arrives in its own migration issue (#97 entity list, #98 graph editor,
// #99 review inbox; audit log and access/settings are not yet filed).
export function StubView({ title }: { title: string }) {
  return (
    <div className="bf-card">
      <h1>{title}</h1>
      <p>Not yet implemented.</p>
    </div>
  );
}
