import { useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { legendKeyOf } from '../lib/geo';
import type { GraphNode } from '../types';

export interface PlaceSearchProps {
  nodes: GraphNode[];
  onChoose(node: GraphNode): void;
}

/* Type a place, arrow through the hits, Enter to route. Searches the same node list
   the map is drawn from, so anything visible on the map is findable here. */
export function PlaceSearch({ nodes, onChoose }: PlaceSearchProps) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<GraphNode[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  const places = nodes.filter((node) => node.kind !== 'waypoint');

  useEffect(() => {
    const onDocumentClick = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('click', onDocumentClick);
    return () => document.removeEventListener('click', onDocumentClick);
  }, []);

  const search = (value: string) => {
    const needle = value.trim().toLowerCase();
    if (needle.length < 2) {
      setHits([]);
      setOpen(false);
      setActive(-1);
      return;
    }
    const found = places
      .filter(
        (node) =>
          node.name.toLowerCase().includes(needle) ||
          (node.service || '').toLowerCase().includes(needle),
      )
      .sort((a, b) => {
        const ai = a.name.toLowerCase().indexOf(needle);
        const bi = b.name.toLowerCase().indexOf(needle);
        return ai - bi || a.name.length - b.name.length;
      })
      .slice(0, 7);
    setHits(found);
    setOpen(true);
    setActive(found.length ? 0 : -1);
  };

  const choose = (index: number) => {
    const hit = hits[index];
    if (!hit) return;
    setOpen(false);
    setQuery('');
    setHits([]);
    onChoose(hit);
  };

  return (
    <div className="absolute left-3 right-[62px] top-3 z-[6]" ref={wrap}>
      <div className="relative">
        <Search
          size={14}
          aria-hidden="true"
          className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted"
        />
        <input
          type="search"
          autoComplete="off"
          spellCheck={false}
          placeholder="Search a place — library, Core D, AED…"
          aria-label="Search for a place on campus"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            search(event.target.value);
          }}
          onFocus={() => {
            if (query.trim().length >= 2) search(query);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' && hits.length) {
              setActive((active + 1) % hits.length);
              event.preventDefault();
            } else if (event.key === 'ArrowUp' && hits.length) {
              setActive((active - 1 + hits.length) % hits.length);
              event.preventDefault();
            } else if (event.key === 'Enter') {
              choose(active >= 0 ? active : 0);
              event.preventDefault();
            } else if (event.key === 'Escape') {
              setOpen(false);
            }
          }}
          className="w-full rounded-[10px] border border-line bg-panel py-2.5 pl-9 pr-3.5 text-[13px] text-ink shadow-[0_6px_18px_var(--accent-ring)] placeholder:text-muted/80 focus:border-brand focus:outline-none"
        />
      </div>

      {open ? (
        <div className="mt-1.5 max-h-[262px] overflow-y-auto rounded-[10px] border border-line bg-panel shadow-[0_14px_30px_var(--accent-ring)]">
          {hits.length ? (
            hits.map((hit, index) => (
              <div
                key={hit.id}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(index);
                }}
                className={
                  'flex cursor-pointer items-center gap-[9px] border-b border-panel-soft px-3 py-[9px] text-[12.5px] last:border-b-0 ' +
                  (index === active ? 'bg-accent-soft' : 'hover:bg-accent-soft')
                }
              >
                <i
                  className="h-2 w-2 flex-none rounded-full bg-brand"
                  style={
                    hit.service
                      ? { background: 'var(--cat-' + legendKeyOf(hit) + ')' }
                      : undefined
                  }
                />
                <span>{hit.name}</span>
                <span className="ml-auto whitespace-nowrap text-[11px] text-muted">
                  {hit.kind === 'facility'
                    ? hit.service || 'facility'
                    : hit.kind === 'entrance'
                      ? 'entrance'
                      : 'place'}
                </span>
              </div>
            ))
          ) : (
            <div className="px-3 py-2.5 text-xs text-muted">No place matches that name.</div>
          )}
        </div>
      ) : null}
    </div>
  );
}
