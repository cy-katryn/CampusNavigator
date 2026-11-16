export type NodeKind = 'building' | 'place' | 'entrance' | 'service' | 'waypoint';

export interface GraphNode {
  id: string;
  name: string;
  lat: number;
  lng: number;
  kind: NodeKind;
  parent?: string | null;
  building?: string | null;
  service?: string | null;
  accepts?: string[];
  floor?: string | null;
}

export interface GraphEdge {
  id: string;
  u: string;
  v: string;
  metres: number;
  covered: boolean;
  stairs: boolean;
  path: [number, number][];
}

/** One drawn shape: a building ring, or a line when `line` is set. */
export interface Footprint {
  id: string;
  ring: [number, number][];
  name?: string;
  kind?: string;
  line?: boolean;
  height?: number;
  tone?: string;
  /** The letter to print on the roof: the block code the data gives, never a guess. */
  display?: string;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  footprints: Footprint[];
}

export interface RouteSegment {
  from: string;
  to: string;
  metres: number;
  path: [number, number][];
}

export interface RoutePoint {
  id: string;
  name: string;
  lat: number;
  lng: number;
}

export interface RouteStats {
  algorithm: string;
  nodes_expanded: number;
  frontier_peak: number;
  ms: number;
}

export interface RoutePayload {
  reachable: boolean;
  metres: number;
  path: RoutePoint[];
  segments: RouteSegment[];
  stats: RouteStats | null;
  explain: unknown[];
}

/** One pickable way there: what the list in the reply shows, and the line to draw. */
export interface RouteOption {
  metres: number;
  legs: number;
  flights: number;
  covered: number;
  via: string;
  start: string;
  goal: string;
  path: RoutePoint[];
  segments: { path: [number, number][] }[];
}

export interface FacilityCard {
  name: string;
  building?: string;
  floor?: string;
  metres?: number;
  hours_today?: string;
  open_now?: boolean;
  lat?: number;
  lng?: number;
  [key: string]: unknown;
}

export interface ChatPayload {
  text: string;
  lead?: string;
  speech?: string;
  narrated?: boolean;
  notes?: string[];
  intent?: string;
  parser?: string;
  route: RoutePayload | null;
  /** Every route worth offering, cheapest first. Empty when the reply has no route. */
  routes?: RouteOption[];
  facilities: FacilityCard[];
  error?: string;
}

export interface KnowledgeStatus {
  documents: number;
  with_opening_hours: number;
}

export interface Status {
  mode: string;
  model: string;
  api_key_present: boolean;
  nodes: number;
  edges: number;
  knowledge?: KnowledgeStatus;
}

export type Role = 'user' | 'bot';

export interface ChatMessage {
  id: number;
  role: Role;
  payload: ChatPayload | string;
}
