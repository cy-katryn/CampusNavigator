/* Endpoint choices for a dictated route. Only the composer may submit them. */
'use strict';

const endpointRoles = ['origin', 'destination'];
const endpointCopy = {
  'en-US': { origin: 'From', destination: 'To', search: 'Search places', choose: 'Select a place', none: 'No matching place', clear: 'Clear' },
  'zh-HK': { origin: '起點', destination: '終點', search: '搜尋地點', choose: '請選擇地點', none: '找不到相符地點', clear: '清除' },
  'zh-CN': { origin: '起点', destination: '终点', search: '搜索地点', choose: '请选择地点', none: '找不到匹配地点', clear: '清除' },
};

class VoiceRouteDraft {
  constructor(panel, graph, language, changed) {
    this.panel = panel;
    this.graph = graph;
    this.language = language;
    this.changed = changed;
    this.places = new Map();
    this.reset();
  }

  reset() {
    clearTimeout(this.debounce);
    this.request?.abort();
    this.request = null;
    this.active = false;
    this.pending = false;
    this.fields = {};
    this.previous = {};
    this.panel.replaceChildren();
    this.panel.hidden = true;
  }

  get blocked() {
    return this.pending || Object.values(this.fields).some((field) => field.editing && !field.id);
  }

  get endpoints() {
    return Object.fromEntries(Object.entries(this.fields)
      .filter(([, field]) => field.id)
      .map(([role, field]) => [role, field.id]));
  }

  get confirmedRoute() {
    if (!Object.values(this.fields).some((field) => field.selected)) return '';
    const lang = this.language();
    const labels = endpointRoles.map((role) => {
      const node = this.places.get(this.fields[role]?.id);
      return node?.names?.[lang] || node?.name || this.fields[role]?.original;
    });
    return labels.every(Boolean) ? labels.join(' → ') : '';
  }

  preview(text, delay = 0, keepChoices = false) {
    const old = keepChoices ? (this.pending ? this.previous : this.fields) : {};
    this.reset();
    if (!text.trim()) { this.changed(); return; }
    this.active = true;
    this.previous = old;
    this.pending = true;
    const request = new AbortController();
    this.request = request;
    this.changed();
    this.debounce = setTimeout(async () => {
      const timeout = setTimeout(() => request.abort(), 5000);
      try {
        const res = await fetch('/api/route-draft', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, lang: this.language() }), signal: request.signal,
        });
        if (!res.ok) throw new Error('Route preview unavailable');
        const draft = await res.json();
        if (this.request !== request || !draft.is_route) return;
        this.places = new Map((this.graph()?.nodes || [])
          .filter((node) => node.kind !== 'waypoint' && node.name)
          .map((node) => [node.id, node]));
        if (!this.places.size) return;
        for (const role of endpointRoles) {
          const parsed = draft[role] || {};
          const saved = old[role];
          const field = saved && saved.original === parsed.text ? saved :
            { original: parsed.text || '', id: this.places.has(parsed.id) ? parsed.id : null, editing: false };
          this.fields[role] = field;
          this.addField(role, field);
        }
        this.panel.hidden = false;
      } catch {
        if (this.request === request) {
          document.getElementById('voice-hint').textContent = 'Place preview unavailable. Review the text before sending.';
        }
      } finally {
        clearTimeout(timeout);
        if (this.request === request) {
          this.request = null;
          this.pending = false;
          this.changed();
        }
      }
    }, delay);
  }

  addField(role, field) {
    const lang = this.language();
    const copy = endpointCopy[lang] || endpointCopy['en-US'];
    const labelFor = (node) => node.names?.[lang] || node.name;
    const wrap = document.createElement('div');
    wrap.className = 'endpoint-field';
    const label = document.createElement('label');
    const id = 'voice-' + role;
    label.htmlFor = id;
    label.textContent = copy[role];
    const input = document.createElement('input');
    input.id = id;
    input.autocomplete = 'off';
    input.placeholder = copy.search;
    input.value = field.id ? labelFor(this.places.get(field.id)) : (field.query ?? field.original);
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = '×';
    clear.setAttribute('aria-label', copy.clear + ' ' + copy[role]);
    const box = document.createElement('div');
    box.className = 'endpoint-options';
    box.hidden = true;
    box.id = id + '-options';
    box.setAttribute('role', 'listbox');
    box.setAttribute('aria-label', copy[role]);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-controls', box.id);
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-autocomplete', 'list');

    let choices = [];
    let active = -1;
    const fold = (value) => String(value || '').normalize('NFKC').toLowerCase().replace(/[\s_-]+/g, '');
    const close = () => {
      box.hidden = true;
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    };
    const showChoiceState = () => {
      input.setAttribute('aria-invalid', String(field.editing && !field.id));
      if (this.blocked) document.getElementById('voice-hint').textContent = copy.choose + '.';
      else document.getElementById('voice-hint').textContent = '';
    };
    const select = (node) => {
      field.id = node.id;
      field.selected = true;
      field.editing = false;
      field.query = '';
      input.value = labelFor(node);
      close();
      showChoiceState();
      this.changed();
    };
    const render = () => {
      const query = fold(input.value);
      const matches = [];
      for (const node of this.places.values()) {
        const names = [node.name, ...Object.values(node.names || {}), ...(node.aliases || []), node.id].map(fold);
        const rank = names.includes(query) ? 0 : names.some(n => n.startsWith(query)) ? 1 :
          names.some(n => n.includes(query)) ? 2 : 3;
        if (rank < 3) matches.push({ node, rank });
      }
      matches.sort((a, b) => a.rank - b.rank || labelFor(a.node).length - labelFor(b.node).length ||
        labelFor(a.node).localeCompare(labelFor(b.node)));
      choices = matches.slice(0, 6).map(match => match.node);
      box.replaceChildren();
      active = -1;
      input.removeAttribute('aria-activedescendant');
      for (const [i, node] of choices.entries()) {
        const item = document.createElement('div');
        item.setAttribute('role', 'option');
        item.id = id + '-option-' + i;
        const name = labelFor(node);
        const duplicate = choices.some(other => other.id !== node.id && labelFor(other) === name);
        const where = [node.building, node.floor].filter(Boolean).join(' · ');
        item.textContent = duplicate ? name + ' · ' + (where || node.id) : name;
        item.addEventListener('mousedown', (ev) => ev.preventDefault());
        item.addEventListener('click', () => select(node));
        box.appendChild(item);
      }
      if (!choices.length) box.textContent = copy.none;
      box.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    };
    const edit = () => {
      field.editing = Boolean(input.value.trim() || field.original || field.id || field.editing);
      field.id = null;
      field.selected = false;
      field.query = input.value;
      render();
      showChoiceState();
      this.changed();
    };
    input.addEventListener('input', edit);
    input.addEventListener('focus', () => { input.select(); render(); });
    input.addEventListener('blur', close);
    clear.addEventListener('click', () => {
      input.value = '';
      edit();
      input.focus();
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') { close(); return; }
      if (ev.key === 'Enter' && !box.hidden && (active >= 0 || choices.length === 1)) {
        ev.preventDefault();
        select(choices[Math.max(0, active)]);
      }
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (box.hidden) render();
        if (!choices.length) return;
        active = ev.key === 'ArrowDown' ? (active + 1) % choices.length :
          (active < 0 ? choices.length - 1 : (active + choices.length - 1) % choices.length);
        Array.from(box.children).forEach((item, i) => item.setAttribute('aria-selected', String(i === active)));
        input.setAttribute('aria-activedescendant', box.children[active].id);
        box.children[active].scrollIntoView({ block: 'nearest' });
      }
    });
    wrap.append(label, input, clear, box);
    this.panel.append(wrap);
  }
}
