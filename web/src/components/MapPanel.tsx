import { useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, Minus, Map as MapIcon, Plus } from 'lucide-react';
import { ALL_CATEGORIES, type Category } from '../lib/geo';
import { CampusMap, type ScaleInfo } from '../lib/campusMap';
import type { FacilityCard, Graph, GraphNode, RouteOption } from '../types';
import { LayerLegend } from './LayerLegend';
import { PlaceSearch } from './PlaceSearch';
import { PANEL, PANEL_HEAD, PANEL_TITLE } from './ui';

export interface MapPanelProps {
  graph: Graph | null;
  routes: RouteOption[];
  routeSelection: number;
  facilities: FacilityCard[];
  mapRef: { current: CampusMap | null };
  onPrefill(text: string): void;
  onSend(text: string): void;
}

const TOOL_BTN =
  'grid h-[34px] w-[34px] cursor-pointer place-items-center rounded-lg border border-line ' +
  'bg-panel text-ink shadow-[0_2px_8px_var(--accent-ring)] transition hover:border-brand';

export function MapPanel(props: MapPanelProps) {
  const svg = useRef<SVGSVGElement>(null);
  const [hidden, setHidden] = useState<Set<Category>>(new Set(ALL_CATEGORIES));
  const [showLinks, setShowLinks] = useState(false);
  const [showDoors, setShowDoors] = useState(false);
  const [basemap, setBasemap] = useState(true);
  const [scale, setScale] = useState<ScaleInfo>({ width: 60, label: '' });
  const [tip, setTip] = useState('');

  // the map is created once; later renders only push it new data
  const handlers = useRef(props);
  handlers.current = props;

  useEffect(() => {
    if (!svg.current) return;
    const map = new CampusMap(svg.current, {
      onScale: setScale,
      onTip: (text) => setTip(text ?? ''),
      onPlaceClick: (node) => handlers.current.onPrefill('how do I get to ' + node.name + '?'),
    });
    props.mapRef.current = map;
    return () => {
      map.dispose();
      props.mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (props.graph) props.mapRef.current?.setGraph(props.graph);
  }, [props.graph, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setRoutes(props.routes, props.routeSelection);
  }, [props.routes, props.routeSelection, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setFacilities(props.facilities);
  }, [props.facilities, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setHiddenCategories(hidden);
  }, [hidden, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setShowLinks(showLinks);
  }, [showLinks, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setShowDoors(showDoors);
  }, [showDoors, props.mapRef]);

  useEffect(() => {
    props.mapRef.current?.setBasemap(basemap);
  }, [basemap, props.mapRef]);

  const chosen = props.routes[props.routeSelection];
  const badge = useMemo(() => {
    if (!chosen) return null;
    return Math.round(chosen.metres) + ' m · ' + chosen.legs + (chosen.legs === 1 ? ' leg' : ' legs');
  }, [chosen]);

  const choosePlace = (node: GraphNode) => {
    props.mapRef.current?.focusPlace(node);
    props.onSend('how do I get to ' + node.name + '?');
  };

  const toggleCategory = (category: Category) => {
    setHidden((previous) => {
      const next = new Set(previous);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  return (
    <section className={PANEL}>
      <div className={PANEL_HEAD}>
        <h2 className={PANEL_TITLE}>Campus map</h2>
        <span className="text-[11px] text-muted">click a place to route to it · scroll to zoom</span>
      </div>

      <div className="relative min-h-0 flex-1 bg-[var(--map-bg)]">
        <svg
          id="map"
          ref={svg}
          role="img"
          aria-label="Map of the PolyU campus showing walkable links and the planned route"
        >
          <g id="layer-tiles" />
          <g id="layer-footprints" />
          <g id="layer-edges" />
          <g id="layer-nodes" />
          <g id="layer-route" />
          <g id="layer-facilities" />
        </svg>

        <PlaceSearch nodes={props.graph ? props.graph.nodes : []} onChoose={choosePlace} />

        {badge ? (
          <div
            data-route-badge
            className="absolute bottom-3 left-1/2 z-[3] -translate-x-1/2 rounded-full bg-[var(--route-1)] px-3 py-[5px] text-[11.5px] font-semibold tracking-[0.02em] text-white shadow-[0_6px_16px_var(--accent-ring)]"
          >
            {badge}
          </div>
        ) : null}

        <LayerLegend
          hidden={hidden}
          showLinks={showLinks}
          showDoors={showDoors}
          onToggleCategory={toggleCategory}
          onToggleLinks={() => setShowLinks((on) => !on)}
          onToggleDoors={() => setShowDoors((on) => !on)}
          onShowAll={() => setHidden(new Set())}
          onHideAll={() => setHidden(new Set(ALL_CATEGORIES))}
        />

        <div
          aria-hidden="true"
          className="absolute bottom-9 right-3.5 flex flex-col items-center text-[10px] font-bold tracking-[0.1em] text-muted"
        >
          N<span className="text-[11px] leading-none">▲</span>
        </div>

        <div className="absolute bottom-9 left-3.5 flex items-center gap-1.5 text-[10.5px] text-muted">
          <b
            className="block h-[5px] w-[60px] border border-b-0 border-muted"
            style={{ width: scale.width + 'px' }}
          />
          <span>{scale.label}</span>
        </div>

        <div className="absolute right-3 top-3 z-[6] flex flex-col gap-1.5">
          <button type="button" title="Zoom in" className={TOOL_BTN} onClick={() => props.mapRef.current?.zoom(0.8)}>
            <Plus size={16} aria-hidden="true" />
          </button>
          <button type="button" title="Zoom out" className={TOOL_BTN} onClick={() => props.mapRef.current?.zoom(1.25)}>
            <Minus size={16} aria-hidden="true" />
          </button>
          <button type="button" title="Fit the whole campus" className={TOOL_BTN} onClick={() => props.mapRef.current?.fitAll()}>
            <Maximize2 size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            title={
              basemap
                ? 'Basemap: street map (click for the illustrated campus)'
                : 'Basemap: illustrated campus (click for the street map)'
            }
            className={TOOL_BTN + (basemap ? ' border-brand bg-accent-soft text-brand' : '')}
            onClick={() => setBasemap((on) => !on)}
          >
            <MapIcon size={15} aria-hidden="true" />
          </button>
        </div>

        <div
          className={
            'pointer-events-none absolute bottom-[30px] left-3 rounded-lg border border-line bg-panel-glass px-[9px] py-[5px] text-[11.5px] text-muted transition-opacity ' +
            (tip ? 'opacity-100' : 'opacity-0')
          }
        >
          {tip}
        </div>

        <footer className="absolute bottom-2 right-3 text-right text-[10.5px] text-muted">
          Basemap © OpenStreetMap contributors · Places: PolyU Campus Map &amp; GreenMap@PolyU
        </footer>
      </div>
    </section>
  );
}
