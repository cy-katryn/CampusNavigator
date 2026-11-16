import { useEffect, useRef, useState } from 'react';
import { categoryOf } from '../lib/geo';
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
    <div className="map-search" ref={wrap}>
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
      />
      {open ? (
        <div className="search-results">
          {hits.length ? (
            hits.map((hit, index) => (
              <div
                key={hit.id}
                className={'search-hit' + (index === active ? ' active' : '')}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(index);
                }}
              >
                <i
                  style={
                    hit.kind === 'service' && hit.service
                      ? { background: 'var(--cat-' + categoryOf(hit) + ')' }
                      : undefined
                  }
                />
                <span>{hit.name}</span>
                <span className="where">
                  {hit.kind === 'service'
                    ? hit.service || 'facility'
                    : hit.kind === 'entrance'
                      ? 'entrance'
                      : 'building'}
                </span>
              </div>
            ))
          ) : (
            <div className="search-empty">No place matches that name.</div>
          )}
        </div>
      ) : null}
    </div>
  );
}
