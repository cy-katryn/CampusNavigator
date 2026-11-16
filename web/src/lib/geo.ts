import type { GraphNode } from '../types';

export const METRES_PER_DEG_LAT = 111320;

/** Deterministic pseudo-random in [0,1) from a string, so decoration does not shimmer. */
export function hash01(text: string, salt: number): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 1000;
}

/** Ray casting on a [lat, lng] ring: is this point inside that polygon? */
export function pointInRing(lat: number, lng: number, ring: [number, number][]): boolean {
  let hit = false;
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [y1, x1] = ring[i];
    const [y2, x2] = ring[i + 1];
    if (y1 > lat !== y2 > lat) {
      if (lng < x1 + ((lat - y1) * (x2 - x1)) / (y2 - y1)) hit = !hit;
    }
  }
  return hit;
}

/** PolyU labels buildings with letters: Core A, Block L, Block VA. */
export function shortCode(name: string): string {
  const core = name.match(/^(?:Core|Block|Wing)\s+([A-Za-z0-9]+)/i);
  if (core) return core[1].toUpperCase();
  const alnum = name.replace(/[^A-Za-z0-9]/g, '');
  return alnum.slice(0, 2).toUpperCase() || '·';
}

/** Cores A-H, J-N, P-T, U-Z form four readable bands on campus. */
export function zoneOf(code: string): 1 | 2 | 3 | 4 {
  const letter = (code || 'A')[0].toUpperCase();
  if (letter <= 'H') return 1;
  if (letter <= 'N') return 2;
  if (letter <= 'T') return 3;
  return 4;
}

export type Category = 'recycle' | 'water' | 'toilet' | 'aed' | 'food' | 'service';

export const ALL_CATEGORIES: Category[] = ['recycle', 'water', 'toilet', 'aed', 'food', 'service'];

/** Which legend colour a facility belongs to, from the service label the engine sends. */
export function categoryOf(node: GraphNode): Category {
  const s = (node.service || '').toLowerCase();
  if (s.includes('toilet')) return 'toilet';
  if (s.includes('water')) return 'water';
  if (s === 'aed') return 'aed';
  if (
    s.includes('recycl') ||
    s.includes('unire') ||
    s.includes('battery') ||
    s.includes('glass') ||
    s.includes('weee') ||
    s.includes('vending') ||
    s.includes('food waste')
  ) {
    return 'recycle';
  }
  if (s.includes('canteen')) return 'food';
  return 'service';
}
