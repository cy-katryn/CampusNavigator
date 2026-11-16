import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { saveSettings } from '../api';
import type { Status } from '../types';
import { BTN_GHOST, BTN_PRIMARY, FIELD_INPUT } from './ui';

export interface SettingsDialogProps {
  open: boolean;
  status: Status | null;
  onClose(): void;
  onSaved(status: Status): void;
}

export function SettingsDialog({ open, status, onClose, onSaved }: SettingsDialogProps) {
  const [key, setKey] = useState('');
  const [model, setModel] = useState('');
  const [message, setMessage] = useState('');
  const [tone, setTone] = useState<'idle' | 'ok' | 'err'>('idle');
  const keyField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setKey('');
    setModel('');
    setTone('idle');
    setMessage(
      status && status.api_key_present
        ? 'A key is already stored. Leave the field blank to keep it.'
        : 'No key stored yet — the app runs on the deterministic parser.',
    );
    keyField.current?.focus();
  }, [open, status]);

  if (!open) return null;

  const save = async () => {
    const trimmedKey = key.trim();
    const trimmedModel = model.trim();
    if (!trimmedKey && !trimmedModel) {
      setMessage('Nothing to save — both fields are empty.');
      setTone('err');
      return;
    }
    setMessage('Saving…');
    setTone('idle');
    try {
      const result = await saveSettings(trimmedKey || null, trimmedModel || null);
      onSaved(result.status);
      setMessage(
        'Saved to ' +
          result.env +
          '. The model is ' +
          (result.status.model ? 'connected: ' + result.status.model : 'still not connected.'),
      );
      setTone('ok');
      setTimeout(onClose, 900);
    } catch (error) {
      setMessage('Could not reach the engine: ' + String(error));
      setTone('err');
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] grid place-items-center bg-[rgba(28,18,21,0.45)] backdrop-blur-[3px]"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className="w-[min(460px,calc(100vw-32px))] rounded-panel border border-line bg-panel px-5 pb-4 pt-[18px] shadow-2xl"
      >
        <header className="flex items-center justify-between">
          <h2 id="settings-title" className="m-0 text-[15px] font-semibold text-ink">
            Model settings
          </h2>
          <button type="button" aria-label="Close" onClick={onClose} className={BTN_GHOST}>
            <X size={15} aria-hidden="true" />
          </button>
        </header>

        <p className="mb-3.5 mt-2 text-xs leading-relaxed text-muted">
          Saved to <code className="font-mono text-[11.5px]">.env</code> in the project folder.
        </p>

        <label className="mb-3 block">
          <span className="mb-1 block text-[11.5px] text-muted">OpenRouter API key</span>
          <input
            ref={keyField}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="sk-or-v1-… (leave blank to keep the current one)"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void save();
            }}
            className={FIELD_INPUT}
          />
        </label>

        <label className="mb-3 block">
          <span className="mb-1 block text-[11.5px] text-muted">Model id</span>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="google/gemma-4-26b-a4b-it:free"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            className={FIELD_INPUT}
          />
        </label>

        <p
          className={
            'mb-3 mt-1 min-h-4 text-xs ' +
            (tone === 'ok' ? 'text-ok' : tone === 'err' ? 'text-danger' : 'text-muted')
          }
        >
          {message}
        </p>

        <footer className="flex justify-end gap-2">
          <button type="button" onClick={() => void save()} className={BTN_PRIMARY}>
            Save to .env
          </button>
          <button type="button" onClick={onClose} className={BTN_GHOST}>
            Cancel
          </button>
        </footer>
      </div>
    </div>
  );
}
