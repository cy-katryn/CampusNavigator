import { useEffect, useMemo, useRef, useState } from 'react';
import { ALL_CATEGORIES, type Category } from '../lib/geo';
import { CampusMap, type ScaleInfo } from '../lib/campusMap';
import type { FacilityCard, Graph, GraphNode, RoutePayload } from '../types';
import { LayerLegend } from './LayerLegend';
import { PlaceSearch } from './PlaceSearch';

export interface MapPanelProps {
  graph: Graph | null;
  route: RoutePayload | null;
  facilities: FacilityCard[];
  mapRef: { current: CampusMap | null };
  onPrefill(text: string): void;
  onSend(text: string): void;
}

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
    props.mapRef.current?.setRoute(props.route);
  }, [props.route, props.mapRef]);

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

  const navigating = Boolean(props.route && props.route.path.length >= 2);
  const badge = useMemo(() => {
    if (!navigating || !props.route) return null;
    return props.route.reachable
      ? Math.round(props.route.metres) + ' m · ' + props.route.segments.length + ' legs'
      : 'no route with those closures';
  }, [navigating, props.route]);

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
    <section className="panel map-panel">
      <div className="panel-head">
        <h2>Campus map</h2>
        <span className="map-hint">click a place to route to it · scroll to zoom</span>
      </div>

      <div className="map-wrap">
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

        {navigating && badge ? (
          <div
            className={
              'route-badge' + (props.route && !props.route.reachable ? ' failed' : '')
            }
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

        <div className="map-north" aria-hidden="true">
          N<span>▲</span>
        </div>
        <div className="map-scale">
          <b style={{ width: scale.width + 'px' }} />
          <span>{scale.label}</span>
        </div>

        <div className="map-tools">
          <button type="button" title="Zoom in" onClick={() => props.mapRef.current?.zoom(0.8)}>
            +
          </button>
          <button type="button" title="Zoom out" onClick={() => props.mapRef.current?.zoom(1.25)}>
            −
          </button>
          <button
            type="button"
            title="Fit the whole campus"
            onClick={() => props.mapRef.current?.fitAll()}
          >
            ⤢
          </button>
          <button
            type="button"
            className={basemap ? 'active' : ''}
            title={
              basemap
                ? 'Basemap: street map (click for the illustrated campus)'
                : 'Basemap: illustrated campus (click for the street map)'
            }
            onClick={() => setBasemap((on) => !on)}
          >
            ▣
          </button>
        </div>

        <div className={'map-tip' + (tip ? ' show' : '')}>{tip}</div>

        <footer className="attrib">
          Basemap © OpenStreetMap contributors · Places: PolyU Campus Map &amp; GreenMap@PolyU
        </footer>
      </div>
    </section>
  );
}
