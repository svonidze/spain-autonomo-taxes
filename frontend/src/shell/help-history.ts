import { router } from './router.ts';
import { helpAdapter } from '../vue/help.ts';
export const shellHelp = helpAdapter;
export function connectHelp(navigate: (url: string) => void) {
  shellHelp().setHistoryAdapter({
    push(id) {
      const current = router.currentRoute.value;
      void router.push({
        path: current.path,
        query: current.query,
        hash: current.hash,
        force: true,
        state: {
          accountingHelp: id,
          contactGlobalPeriod: window.history.state?.contactGlobalPeriod ?? null,
        },
      });
    },
    back: () => router.back(),
    clear() {
      const current = router.currentRoute.value;
      void router.replace({
        path: current.path,
        query: current.query,
        hash: current.hash,
        force: true,
        state: { accountingHelp: null },
      });
    },
    navigate,
  });
}
