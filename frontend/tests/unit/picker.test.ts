import { test, expect, vi } from 'vitest';
import { loadPicker, chooseFolder } from '../../src/features/intake/picker.ts';
test('Picker loader can recover after a failed script without storing credentials', async () => {
  const target = window as unknown as {
    google?: unknown;
    gapi?: { load: (module: string, callback: () => void) => void };
  };
  delete target.google;
  delete target.gapi;
  const failed = loadPicker();
  const assertion = expect(failed).rejects.toThrow('intake.googlePickerUnavailable');
  const script = document.querySelector<HTMLScriptElement>('script[data-google-picker-api]')!;
  expect(script.src).toBe('https://apis.google.com/js/api.js');
  script.dispatchEvent(new Event('error'));
  await assertion;
  expect(script.isConnected).toBe(false);
  let callback!: (data: Record<string, unknown>) => void;
  const visible = vi.fn(),
    dispose = vi.fn();
  class View {
    setSelectFolderEnabled() {
      return this;
    }
    setMode() {
      return this;
    }
  }
  class Builder {
    addView() {
      return this;
    }
    setOAuthToken() {
      return this;
    }
    setDeveloperKey() {
      return this;
    }
    setAppId() {
      return this;
    }
    setOrigin() {
      return this;
    }
    setLocale() {
      return this;
    }
    setCallback(value: typeof callback) {
      callback = value;
      return this;
    }
    build() {
      return { setVisible: visible, dispose };
    }
  }
  const api = {
    DocsView: View,
    PickerBuilder: Builder,
    ViewId: { FOLDERS: 'folders' },
    DocsViewMode: { LIST: 'list' },
    Action: { PICKED: 'picked', CANCEL: 'cancel' },
    Response: { DOCUMENTS: 'docs' },
    Document: { ID: 'id', NAME: 'name' },
  };
  target.gapi = {
    load: (_module, done) => {
      target.google = { picker: api };
      done();
    },
  };
  const loaded = await loadPicker();
  expect(loaded).toBe(api);
  expect(document.querySelector('script[data-google-picker-api]')).toBeNull();
  const picker = chooseFolder(
    loaded,
    {
      enabled: true,
      developer_key: 'synthetic-key',
      app_id: 'synthetic-app',
      access_token: 'synthetic-access-token',
    },
    'en',
  );
  callback({ action: 'picked', docs: [{ id: 'too-short', name: 'Ignored' }] });
  expect(await picker.result).toBeNull();
  picker.close();
  picker.close();
  expect(visible.mock.calls).toEqual([[true], [false]]);
  expect(dispose).toHaveBeenCalledTimes(1);
  delete target.google;
  delete target.gapi;
});
