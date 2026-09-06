import type { ExpensePage } from './model.ts';
export function createExpensePager(
  fetchPage: (query: { query: string; offset: number }) => Promise<ExpensePage>,
) {
  let version = 0,
    current: ExpensePage | null = null,
    currentQuery = '';
  async function load(
    query: string,
    append = false,
    targetCount = 0,
    restarts = 0,
  ): Promise<ExpensePage | null> {
    const request = ++version,
      previous = append && query === currentQuery ? current : null;
    try {
      let offset = previous?.next_offset || 0,
        rows = previous?.rows || [],
        snapshot = previous,
        page: ExpensePage;
      do {
        page = await fetchPage({ query, offset });
        if (request !== version) return null;
        if (
          snapshot &&
          (snapshot.as_of !== page.as_of || snapshot.view_revision !== page.view_revision)
        ) {
          // A server whose revision moves on every page never converges; report instead of looping.
          if (restarts >= 3) throw new Error('expense.invalidPage');
          return load(
            query,
            false,
            Math.max(targetCount, rows.length + (append ? page.rows.length : 0)),
            restarts + 1,
          );
        }
        snapshot = page;
        rows = [
          ...new Map([...rows, ...page.rows].map((row) => [row.transaction_id, row])).values(),
        ];
        if (page.has_more && page.next_offset <= offset) throw new Error('expense.invalidPage');
        offset = page.next_offset;
      } while (page.has_more && rows.length < targetCount);
      current = { ...page, rows };
      currentQuery = query;
      return current;
    } catch (error) {
      if (request !== version) return null;
      throw error;
    }
  }
  return {
    load,
    refresh: (query: string) =>
      load(query, false, query === currentQuery ? current?.rows.length || 0 : 0),
    invalidate: () => {
      version++;
    },
  };
}
