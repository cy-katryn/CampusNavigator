import type { ChatMessage, ChatPayload, FacilityCard, RouteOption } from '../types';

function Readable({ text }: { text: string }) {
  // Keep the engineer-readable part (segment table, search stats) visually quieter.
  return (
    <span>
      {text.split('\n').map((line, index) => {
        const quiet = /^\s*[-…]/.test(line) || /^(Search|Total|Route:)/.test(line) || !line.trim();
        return (
          <span key={index} className={quiet ? 'text-[12.5px] text-muted' : undefined}>
            {(index ? '\n' : '') + line}
          </span>
        );
      })}
    </span>
  );
}

function Summary({ payload }: { payload: ChatPayload }) {
  const bits: string[] = [];
  const route = payload.route;
  if (route && route.reachable) {
    bits.push(route.segments.length + ' legs');
    if (route.stats) {
      bits.push(
        route.stats.algorithm.toUpperCase() +
          ' expanded ' +
          route.stats.nodes_expanded +
          ' nodes, ' +
          route.stats.ms +
          ' ms',
      );
    }
  }
  if ((payload.facilities || []).length > 1) bits.push(payload.facilities.length + ' places');
  return (
    <summary className="summary text-[11.5px] tracking-[0.04em] text-muted hover:text-ink">
      {bits.length ? bits.join(' · ') : 'details'}
    </summary>
  );
}

const TAG =
  'rounded-full border border-line px-2 py-0.5 text-[10.5px] uppercase tracking-[0.06em] text-muted';

function FacilityCards({
  facilities,
  onPick,
}: {
  facilities: FacilityCard[];
  onPick(name: string): void;
}) {
  return (
    <div className="mt-1 flex flex-col gap-1.5">
      {facilities.map((facility, index) => {
        const where = [facility.building, facility.floor].filter(Boolean).join(', ');
        const bits: string[] = [];
        if (where) bits.push(where);
        if (facility.metres != null) bits.push(facility.metres + ' m');
        if (facility.hours_today) {
          bits.push((facility.open_now === false ? 'closed · ' : 'open · ') + facility.hours_today);
        }
        return (
          <div
            key={facility.name + index}
            className="cursor-pointer rounded-[10px] border border-line bg-panel-soft px-2.5 py-2 text-[12.5px] transition hover:border-brand"
            title={'Ask for a route to ' + facility.name}
            onClick={() => onPick(facility.name)}
          >
            <b className="font-semibold text-brand-dark">{facility.name}</b>
            {bits.length ? <div className="mt-0.5 text-[11.5px] text-muted">{bits.join(' · ')}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

function RouteOptions({
  routes,
  selection,
  onSelect,
}: {
  routes: RouteOption[];
  selection: number | null;
  onSelect(index: number): void;
}) {
  if (routes.length < 2) return null;
  return (
    <div className="mt-2 flex flex-col gap-1">
      {routes.map((route, index) => {
        const facts = [
          route.legs + (route.legs === 1 ? ' leg' : ' legs'),
          route.flights
            ? route.flights + (route.flights === 1 ? ' flight of steps' : ' flights of steps')
            : 'no steps',
          route.via ? 'via ' + route.via : '',
        ].filter(Boolean);
        return (
          <button
            key={index}
            type="button"
            title={'Show route ' + (index + 1) + ' on the map'}
            onClick={() => onSelect(index)}
            data-route-option={index}
            data-selected={selection === index ? 'true' : 'false'}
            className={
              'flex w-full cursor-pointer items-center gap-2 rounded-[10px] border px-2.5 py-[7px] ' +
              'text-left text-[12.5px] transition ' +
              (selection === index
                ? 'border-brand bg-accent-soft shadow-[inset_2px_0_0_var(--accent)]'
                : 'border-line bg-panel-soft hover:border-brand')
            }
          >
            <i
              className="h-[3px] w-3.5 flex-none rounded-[2px]"
              style={{ background: 'var(--route-' + (index + 1) + ')' }}
            />

            <span className="font-semibold">{Math.round(route.metres)} m</span>
            <span className="ml-auto text-right text-muted">{facts.join(' · ')}</span>
          </button>
        );
      })}
    </div>
  );
}

export interface MessageProps {
  message: ChatMessage;
  /** Which option of THIS message is currently on the map, or null. */
  selection: number | null;
  onFacilityClick(name: string): void;
  onSelectRoute(index: number): void;
}

export function Message({ message, selection, onFacilityClick, onSelectRoute }: MessageProps) {
  const { role, payload } = message;

  if (role === 'user' || typeof payload === 'string') {
    return (
      <div className={'flex max-w-full flex-col gap-1.5 ' + (role === 'user' ? 'items-end' : '')}>
        <div className="text-[11px] uppercase tracking-[0.1em] text-muted">
          {role === 'user' ? 'you' : 'navigator'}
        </div>
        <div
          className={
            'whitespace-pre-wrap break-words rounded-[13px] px-[13px] py-[11px] text-sm leading-relaxed ' +
            (role === 'user'
              ? 'rounded-tr-[4px] bg-brand text-onaccent'
              : 'rounded-tl-[4px] border border-line bg-panel-soft text-ink')
          }
        >
          {String(payload)}
        </div>
      </div>
    );
  }

  const tags = payload.notes ?? [];

  return (
    <div className="flex max-w-full flex-col gap-1.5">
      <div className="text-[11px] uppercase tracking-[0.1em] text-muted">navigator</div>
      <div className="whitespace-pre-wrap break-words rounded-[13px] rounded-tl-[4px] border border-line bg-panel-soft px-[13px] py-[11px] text-sm leading-relaxed text-ink">
        {payload.lead ? (
          <>
            <div className="text-[14.5px] leading-relaxed">{payload.lead}</div>
            <details className="mt-2 border-t border-dashed border-line pt-1.5">
              <Summary payload={payload} />
              <span className="mt-1.5 block">
                <Readable text={payload.text} />
              </span>
            </details>
          </>
        ) : (
          <Readable text={payload.text} />
        )}

        {payload.routes && payload.routes.length ? (
          <RouteOptions routes={payload.routes} selection={selection} onSelect={onSelectRoute} />
        ) : null}

        {payload.narrated || payload.parser || tags.length ? (
          <div className="flex flex-wrap gap-1.5">
            {payload.narrated ? <span className={TAG + ' border-line-strong text-brand'}>prose: model · numbers: A*</span> : null}
            {payload.parser ? (
              <span className="text-[10px] italic text-muted opacity-75">understood by {payload.parser}</span>
            ) : null}
            {tags.map((note, index) => (
              <span key={note + index} className={TAG}>
                {note}
              </span>
            ))}
          </div>
        ) : null}

        {payload.facilities && payload.facilities.length ? (
          <FacilityCards facilities={payload.facilities} onPick={onFacilityClick} />
        ) : null}
      </div>
    </div>
  );
}
