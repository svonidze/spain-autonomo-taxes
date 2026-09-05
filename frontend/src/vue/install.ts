import {mountView} from './host.ts';
import ExpenseDetail from '../features/expense-detail/ExpenseDetail.vue';
import type {ExpenseDetailContext} from '../features/expense-detail/model.ts';
const views = {expenseDetail: (root: HTMLElement, context: ExpenseDetailContext) => mountView(root, ExpenseDetail, context)};
declare global {var AutonomoViews: typeof views;}
globalThis.AutonomoViews = views;
