import { LEGEND } from '../lib/geo';

const CHIP =
  'inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line bg-panel px-2 py-[3px] ' +
  'font-sans text-[10.5px] text-muted transition hover:border-line-strong hover:text-ink';

const HEAD =
  'm-0 text-[9.5px] font-semibold uppercase tracking-[0.09em] text-muted/80';

export interface LayerLegendProps {
  hidden: Set<string>;
  showLinks: boolean;
  showDoors: boolean;
  onToggleCategory(key: string): void;
  onToggleCategoryGroup(section: string, on: boolean): void;
  onToggleLinks(): void;
  onToggleDoors(): void;
  onShowAll(): void;
  onHideAll(): void;
}

function stateClass(off: boolean): string {
  return off ? ' opacity-40 line-through' : '';
}

/* The legend sits in the bottom-left corner the way a printed campus map carries its
   key. Chips are grouped the way the campus map publishes its places: by category,
   and under Amenities by the amenity itself. */
export function LayerLegend(props: LayerLegendProps) {
  const { hidden } = props;

  const anyShown = (section: string): boolean => {
    const entries = LEGEND.find((item) => item.id === section)?.entries || [];
    return entries.some((entry) => !hidden.has(entry.key));
  };

  return (
    <div className="absolute bottom-[30px] left-3 z-[4] flex w-max max-w-[264px] flex-col gap-1.5 rounded-xl border border-line bg-panel-glass px-[11px] py-[9px] text-[11px] text-muted shadow-[0_6px_18px_var(--accent-ring)] backdrop-blur-[6px]">
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

      {LEGEND.map((section) => (
        <div key={section.id} className="flex max-w-[238px] flex-col gap-1">
          <button
            type="button"
            onClick={() => props.onToggleCategoryGroup(section.id, !anyShown(section.id))}
            className={HEAD + ' w-max cursor-pointer hover:text-ink'}
            title={'Show or hide every ' + section.label.toLowerCase() + ' chip'}
          >
            {section.label}
          </button>
          <div className="flex flex-wrap gap-[5px]">
            {section.entries.map((entry) => (
              <button
                key={entry.key}
                type="button"
                data-cat={entry.key}
                onClick={() => props.onToggleCategory(entry.key)}
                className={CHIP + stateClass(hidden.has(entry.key))}
              >
                <i
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: 'var(--cat-' + entry.key + ')' }}
                />
                {entry.label}
              </button>
            ))}
          </div>
        </div>
      ))}

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
