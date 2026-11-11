import { useEffect, useState } from 'react';

export function usePwaUpdate() {
  const [updateReady, setUpdateReady] = useState(false);

  useEffect(() => {
    if (!import.meta.env.PROD || import.meta.env.MODE === 'windows-launcher'
      || !window.isSecureContext || !('serviceWorker' in navigator)) return;

    const events = new AbortController();
    const { signal } = events;
    const scope = new URL(import.meta.env.BASE_URL, document.baseURI);

    void navigator.serviceWorker.register(new URL('sw.js', scope), { scope: scope.href, updateViaCache: 'none' })
      .then((registration) => {
        if (signal.aborted) return;

        const showWaitingUpdate = () => {
          setUpdateReady(Boolean(registration.active && registration.waiting));
        };
        const watchInstall = () => {
          registration.installing?.addEventListener('statechange', showWaitingUpdate, { signal });
        };

        registration.addEventListener('updatefound', watchInstall, { signal });
        navigator.serviceWorker.addEventListener('controllerchange', showWaitingUpdate, { signal });
        window.addEventListener('focus', () => {
          void registration.update().catch(() => undefined);
        }, { signal });
        watchInstall();
        showWaitingUpdate();
      }).catch((error: unknown) => console.warn('Offline app setup failed.', error));

    return () => events.abort();
  }, []);

  return updateReady;
}
