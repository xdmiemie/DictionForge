import {useEffect, useState, type ReactNode} from 'react';

export const PAGE_SIZE = 5;

export function Pagination({label, page, pages, total, onPage, disabled = false}: {
  label: string; page: number; pages: number; total: number; onPage: (page: number) => void; disabled?: boolean;
}) {
  return <div className="pagination actions" role="group" aria-label={label + '分页'}>
    <button type="button" disabled={disabled || page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
    <span aria-live="polite">第 {page} / {Math.max(1, pages)} 页 · 共 {total} 项 · 每页 {PAGE_SIZE} 项</span>
    <button type="button" disabled={disabled || page >= pages} onClick={() => onPage(page + 1)}>下一页</button>
  </div>;
}

// Only the visible records are mounted. Editing and selection state belongs to
// the caller so changing pages never discards a draft or changes an action's scope.
export function PaginatedList<T>({items, label, children, revealIndex = -1}: {
  items: readonly T[]; label: string; children: (item: T, index: number) => ReactNode; revealIndex?: number;
}) {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const revealPage = revealIndex >= 0 ? Math.floor(revealIndex / PAGE_SIZE) + 1 : undefined;
  const [requestedPage, setPage] = useState(revealPage ?? 1);
  const page = Math.min(requestedPage, pages);
  useEffect(() => {setPage(current => Math.min(current, pages));}, [pages]);
  // Follow a newly selected/playing item, while allowing manual browsing when
  // the selection is unchanged. Polling an identical list must not reset pages.
  useEffect(() => {if (revealPage !== undefined) setPage(revealPage);}, [revealIndex, revealPage]);
  const start = (page - 1) * PAGE_SIZE;
  return <>{items.slice(start, start + PAGE_SIZE).map((item, index) => children(item, start + index))}
    {items.length > 0 && <Pagination label={label} page={page} pages={pages} total={items.length} onPage={setPage}/>}
  </>;
}
