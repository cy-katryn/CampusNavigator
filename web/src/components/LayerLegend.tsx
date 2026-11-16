import { ALL_CATEGORIES, type Category } from '../lib/geo';

const CATEGORY_LABELS: Record<Category, string> = {
  recycle: 'recycling',
  water: 'water',
  toilet: 'toilet',
  aed: 'AED',
  food: 'canteen',
  service: 'clinic · other',
};

export interface LayerLegendProps {
  hidden: Set<Category>;
  showLinks: boolean;
  showDoors: boolean;
  onToggleCategory(category: Category): void;
  onToggleLinks(): void;
  onToggleDoors(): void;
  onShowAll(): void;
  onHideAll(): void;
}

/* The legend lives in the bottom-left corner and is always on. Its width is
   max-content, so it grows and shrinks with the number of toggles. */
export function LayerLegend(props: LayerLegendProps) {
  const { hidden } = props;
  return (
    <div className="map-legend">
      <div className="legend-row">
        <button
          className={'legend-chip' + (props.showLinks ? '' : ' off')}
          type="button"
          onClick={props.onToggleLinks}
        >
          <i className="sw-line" />
          walkway links
        </button>
        <button
          className={'legend-chip' + (props.showDoors ? '' : ' off')}
          type="button"
          onClick={props.onToggleDoors}
        >
          <i className="sw-door" />
          doorways
        </button>
      </div>

      <div className="legend-cats">
        {ALL_CATEGORIES.map((category) => (
          <button
            key={category}
            className={'legend-chip' + (hidden.has(category) ? ' off' : '')}
            data-cat={category}
            type="button"
            onClick={() => props.onToggleCategory(category)}
          >
            <i />
            {CATEGORY_LABELS[category]}
          </button>
        ))}
      </div>

      <div className="legend-actions">
        <button type="button" onClick={props.onShowAll}>
          Show all
        </button>
        <button type="button" onClick={props.onHideAll}>
          Hide all
        </button>
      </div>
    </div>
  );
}
