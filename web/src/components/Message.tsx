import type { ChatMessage, ChatPayload, FacilityCard } from '../types';

function Readable({ text }: { text: string }) {
  // Keep the engineer-readable part (segment table, search stats) visually quieter.
  return (
    <span>
      {text.split('\n').map((line, index) => {
        const quiet = /^\s*[-…]/.test(line) || /^(Search|Total|Route:)/.test(line) || !line.trim();
        return (
          <span key={index} className={quiet ? 'seg' : undefined}>
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
  return <summary>{bits.length ? bits.join(' · ') : 'details'}</summary>;
}

function FacilityCards({
  facilities,
  onPick,
}: {
  facilities: FacilityCard[];
  onPick(name: string): void;
}) {
  return (
    <div className="cards">
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
            className="card"
            title={'Ask for a route to ' + facility.name}
            onClick={() => onPick(facility.name)}
          >
            <b>{facility.name}</b>
            {bits.length ? <div className="meta">{bits.join(' · ')}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

export interface MessageProps {
  message: ChatMessage;
  onFacilityClick(name: string): void;
}

export function Message({ message, onFacilityClick }: MessageProps) {
  const { role, payload } = message;

  if (role === 'user' || typeof payload === 'string') {
    return (
      <div className={'msg ' + role}>
        <div className="who">{role === 'user' ? 'you' : 'navigator'}</div>
        <div className="bubble">{String(payload)}</div>
      </div>
    );
  }

  const tags: string[] = [];
  if (payload.notes) tags.push(...payload.notes);

  return (
    <div className="msg bot">
      <div className="who">navigator</div>
      <div className="bubble">
        {payload.lead ? (
          <>
            <div className="lead">{payload.lead}</div>
            <details>
              <Summary payload={payload} />
              <Readable text={payload.text} />
            </details>
          </>
        ) : (
          <Readable text={payload.text} />
        )}

        {payload.narrated || payload.parser || tags.length ? (
          <div className="tags">
            {payload.narrated ? <span className="tag llm">prose: model · numbers: A*</span> : null}
            {payload.parser ? <span className="tag-parser">understood by {payload.parser}</span> : null}
            {tags.map((note, index) => (
              <span key={note + index} className="tag">
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
