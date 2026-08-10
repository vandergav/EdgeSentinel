interface PlaceholderPanelProps {
  title: string;
  description: string;
}

/** Reusable "coming soon" card — used by the Dashboard and Settings
 * placeholders so future phases have one consistent, honest way to mark
 * a feature as scaffolded-but-not-built, instead of each page inventing
 * its own convention. */
export function PlaceholderPanel({ title, description }: PlaceholderPanelProps) {
  return (
    <div className="placeholder-panel">
      <span className="placeholder-panel__badge">Coming soon</span>
      <h3 className="placeholder-panel__title">{title}</h3>
      <p className="placeholder-panel__description">{description}</p>
    </div>
  );
}
