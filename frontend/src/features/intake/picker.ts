import type { Folder, PickerConfig } from './model.ts';
interface DocsView {
  setSelectFolderEnabled(value: boolean): DocsView;
  setMode(value: string): DocsView;
}
interface Picker {
  setVisible(value: boolean): void;
  dispose?(): void;
}
interface Builder {
  addView(view: DocsView): Builder;
  setOAuthToken(token: string): Builder;
  setDeveloperKey(key: string): Builder;
  setAppId(id: string): Builder;
  setOrigin(origin: string): Builder;
  setLocale?(locale: string): Builder;
  setCallback(callback: (data: Record<string, unknown>) => void): Builder;
  build(): Picker;
}
interface PickerApi {
  DocsView: new (id: string) => DocsView;
  PickerBuilder: new () => Builder;
  ViewId: { FOLDERS: string };
  DocsViewMode: { LIST: string };
  Action: { PICKED: string; CANCEL: string };
  Response: { DOCUMENTS: string };
  Document: { ID: string; NAME: string };
}
interface Globals {
  google?: { picker?: PickerApi };
  gapi?: { load(module: string, callback: () => void): void };
}
const globals = () => window as unknown as Globals;
let loading: Promise<PickerApi> | undefined;
export function loadPicker(): Promise<PickerApi> {
  const ready = globals().google?.picker;
  if (ready) return Promise.resolve(ready);
  if (loading) return loading;
  loading = new Promise<PickerApi>((resolve, reject) => {
    let script: HTMLScriptElement | undefined,
      finished = false;
    const end = (error?: Error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (error) {
        script?.remove();
        reject(error);
      } else {
        const api = globals().google?.picker;
        if (api) resolve(api);
        else reject(new Error('intake.googlePickerUnavailable'));
      }
    };
    const timer = setTimeout(() => end(new Error('intake.googlePickerUnavailable')), 15000);
    const modules = () => {
      if (finished) return;
      try {
        const gapi = globals().gapi;
        if (!gapi) {
          end(new Error('intake.googlePickerUnavailable'));
          return;
        }
        gapi.load('picker', () => end());
      } catch {
        end(new Error('intake.googlePickerUnavailable'));
      }
    };
    if (globals().gapi) {
      modules();
      return;
    }
    const existing = document.querySelector<HTMLScriptElement>('script[data-google-picker-api]');
    script = existing || document.createElement('script');
    script.addEventListener('load', modules, { once: true });
    script.addEventListener('error', () => end(new Error('intake.googlePickerUnavailable')), {
      once: true,
    });
    if (!existing) {
      script.src = 'https://apis.google.com/js/api.js';
      script.async = true;
      script.dataset.googlePickerApi = 'true';
      document.head.append(script);
    }
  }).catch((error) => {
    loading = undefined;
    throw error;
  });
  return loading;
}
export function chooseFolder(
  api: PickerApi,
  config: PickerConfig,
  locale: string,
): { result: Promise<Folder | null>; close(): void } {
  if (!config.enabled || !config.access_token || !config.developer_key || !config.app_id)
    throw new Error('intake.googlePickerUnavailable');
  let finish!: (folder: Folder | null) => void,
    settled = false;
  const result = new Promise<Folder | null>((resolve) => {
    finish = (value) => {
      if (!settled) {
        settled = true;
        resolve(value);
      }
    };
  });
  const view = new api.DocsView(api.ViewId.FOLDERS)
    .setSelectFolderEnabled(true)
    .setMode(api.DocsViewMode.LIST);
  const builder = new api.PickerBuilder()
    .addView(view)
    .setOAuthToken(config.access_token)
    .setDeveloperKey(config.developer_key)
    .setAppId(config.app_id)
    .setOrigin(window.location.origin);
  builder.setLocale?.(locale);
  const picker = builder
    .setCallback((data) => {
      if (data.action === api.Action.CANCEL) {
        finish(null);
        return;
      }
      if (data.action !== api.Action.PICKED) return;
      const values = data[api.Response.DOCUMENTS],
        document = Array.isArray(values)
          ? (values[0] as Record<string, unknown> | undefined)
          : undefined;
      const id = String(document?.[api.Document.ID] || ''),
        name = String(document?.[api.Document.NAME] || id);
      finish(/^[A-Za-z0-9_-]{10,256}$/.test(id) ? { id, name } : null);
    })
    .build();
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    picker.setVisible(false);
    picker.dispose?.();
    finish(null);
  };
  try {
    picker.setVisible(true);
  } catch (error) {
    try {
      close();
    } catch {
      /* Keep the original opening error. */
    }
    throw error;
  }
  return { result, close };
}
