import { useEffect, useRef, useState } from 'react';
import { saveSettings } from '../api';
import type { Status } from '../types';

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
      className="modal"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header>
          <h2 id="settings-title">Model settings</h2>
          <button className="ghost" type="button" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>
        <p className="modal-note">
          Saved to <code>.env</code> in the project folder.
        </p>
        <label className="field">
          <span>OpenRouter API key</span>
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
          />
        </label>
        <label className="field">
          <span>Model id</span>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="google/gemma-4-26b-a4b-it:free"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
        </label>
        <p className={'modal-status' + (tone === 'idle' ? '' : ' ' + tone)}>{message}</p>
        <footer>
          <button type="button" onClick={() => void save()}>
            Save to .env
          </button>
          <button className="ghost" type="button" onClick={onClose}>
            Cancel
          </button>
        </footer>
      </div>
    </div>
  );
}
