import { ALL_CATEGORIES, type Category } from '../lib/geo';

const CATEGORY_LABELS: Record<Category, string> = {
  recycle: 'recycling',
  water: 'water',
  toilet: 'toilet',
  aed: 'AED',
  food: 'canteen',
  service: 'clinic · other',
};

const CHIP =
  'inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line bg-panel px-2 py-[3px] ' +
  'font-sans text-[10.5px] text-muted transition hover:border-line-strong hover:text-ink';

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

function stateClass(off: boolean): string {
  return off ? ' opacity-40 line-through' : '';
}

/* The legend sits in the bottom-left corner the way a printed campus map carries its
   key. `w-max` keeps the panel exactly as wide as the chips in it. */
export function LayerLegend(props: LayerLegendProps) {
  const { hidden } = props;
  return (
    <div className="absolute bottom-[30px] left-3 z-[4] flex w-max max-w-[260px] flex-col gap-1.5 rounded-xl border border-line bg-panel-glass px-[11px] py-[9px] text-[11px] text-muted shadow-[0_6px_18px_var(--accent-ring)] backdrop-blur-[6px]">
      <div className="flex flex-wrap items-center gap-[5px]">
        <button
          type="button"
          onClick={props.onToggleLinks}
          className={CHIP + stateClass(!props.showLinks)}
        >
          <i className="inline-block h-[3px] w-3 rounded-[2px] bg-line" />
          walkway links
        </button>
        <button
          type="button"
          onClick={props.onToggleDoors}
          className={CHIP + stateClass(!props.showDoors)}
        >
          <i className="inline-block h-2 w-2 rounded-sm bg-brand" />
          doorways
        </button>
      </div>

      <div className="flex max-w-[196px] flex-wrap gap-[5px]">
        {ALL_CATEGORIES.map((category) => (
          <button
            key={category}
            type="button"
            data-cat={category}
            onClick={() => props.onToggleCategory(category)}
            className={CHIP + stateClass(hidden.has(category))}
          >
            <i
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: 'var(--cat-' + category + ')' }}
            />
            {CATEGORY_LABELS[category]}
          </button>
        ))}
      </div>

      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={props.onShowAll}
          className="flex-1 cursor-pointer rounded-lg border border-line bg-panel py-1 font-sans text-[10.5px] text-muted transition hover:border-line-strong hover:text-ink"
        >
          Show all
        </button>
        <button
          type="button"
          onClick={props.onHideAll}
          className="flex-1 cursor-pointer rounded-lg border border-line bg-panel py-1 font-sans text-[10.5px] text-muted transition hover:border-line-strong hover:text-ink"
        >
          Hide all
        </button>
      </div>
    </div>
  );
}
