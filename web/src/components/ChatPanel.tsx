import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../types';
import { AvatarDock, type AvatarState } from './AvatarDock';
import { Message } from './Message';

export interface ChatPanelProps {
  messages: ChatMessage[];
  draft: string;
  avatar: AvatarState;
  voiceOn: boolean;
  lang: string;
  micAvailable: boolean;
  micRecording: boolean;
  hint: string;
  /** Increments when something outside the panel wants the composer focused. */
  focusSignal: number;
  onDraftChange(value: string): void;
  onSend(text: string): void;
  onReset(): void;
  onFacilityClick(name: string): void;
  onMic(): void;
  onToggleVoice(): void;
  onLangChange(lang: string): void;
}

export function ChatPanel(props: ChatPanelProps) {
  const chat = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const node = chat.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [props.messages]);

  useEffect(() => {
    if (props.focusSignal > 0) composer.current?.focus();
  }, [props.focusSignal]);

  return (
    <section className="panel chat-panel">
      <div className="panel-head">
        <h2>Chat</h2>
        <button className="ghost" type="button" onClick={props.onReset}>
          Reset
        </button>
      </div>

      <div className="chat" ref={chat}>
        {props.messages.map((message) => (
          <Message key={message.id} message={message} onFacilityClick={props.onFacilityClick} />
        ))}
      </div>

      <AvatarDock
        state={props.avatar}
        voiceOn={props.voiceOn}
        lang={props.lang}
        micAvailable={props.micAvailable}
        micRecording={props.micRecording}
        hint={props.hint}
        onMic={props.onMic}
        onToggleVoice={props.onToggleVoice}
        onLangChange={props.onLangChange}
      />

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSend(props.draft);
        }}
      >
        <input
          ref={composer}
          type="text"
          autoComplete="off"
          placeholder="Ask about a route, a facility, or opening hours…"
          value={props.draft}
          onChange={(event) => props.onDraftChange(event.target.value)}
        />
        <button type="submit">Send</button>
      </form>
    </section>
  );
}
