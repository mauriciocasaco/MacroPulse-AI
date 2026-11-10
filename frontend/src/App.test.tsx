// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from './App';

vi.mock('./MacroWorkspace', () => ({
  MacroWorkspace: ({ analysisEnabled }: { analysisEnabled: boolean }) =>
    <section aria-label="Macro workspace">Macro research {analysisEnabled ? 'AI enabled' : 'AI disabled'}</section>,
}));

afterEach(() => {
  cleanup();
  window.history.replaceState(null, '', '/');
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each(['/', '/?workspace=market', '/?workspace=market&asset=GC%3DF', '/?workspace=macro']) (
  'opens only MacroPulse for the existing entry point %s', (path) => {
    window.history.replaceState(null, '', path);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<App macroAiEnabled />);
    expect(screen.getByRole('region', { name: 'Macro workspace' }).textContent).toContain('AI enabled');
    expect(screen.queryByRole('navigation', { name: 'Workspaces' })).toBeNull();
    expect(screen.queryByText(/Market archive/i)).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Asset' })).toBeNull();
    expect(screen.queryByText('Price History')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it('keeps the public macro feature gate without falling back to the archive', () => {
  vi.stubEnv('MODE', 'public-demo');
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const view = render(<App macroDemoEnabled={false} />);
  expect(screen.getByText('MacroPulse research is unavailable in this environment.')).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  view.rerender(<App macroDemoEnabled macroAiEnabled />);
  expect(screen.getByRole('region').textContent).toContain('AI disabled');
  expect(screen.queryByText(/synthetic|archive/i)).toBeNull();
});

it('keeps the shell offline and restores macro research after reconnecting', () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  render(<App />);
  expect(screen.getByText('You are offline. Live macroeconomic data is unavailable.')).toBeTruthy();
  fireEvent(window, new Event('online'));
  expect(screen.getByRole('region', { name: 'Macro workspace' })).toBeTruthy();
  fireEvent(window, new Event('offline'));
  expect(screen.queryByRole('region')).toBeNull();
});
