import TransactionListsView from '../features/transactions/TransactionListsView.vue';
import {buildIncomeCopy} from '../features/transactions/income-copy.ts';
import {copyable, type TransactionListContext} from '../features/transactions/model.ts';
import ContactsView from '../features/contacts/ContactsView.vue';
import type {ContactsContext} from '../features/contacts/model.ts';
import {mountView} from './host.ts';
import ExpenseDetail from '../features/expense-detail/ExpenseDetail.vue';
import type {ExpenseDetailContext} from '../features/expense-detail/model.ts';
const views = {buildIncomeCopy, copyable, transactions: (root: HTMLElement, context: TransactionListContext) => mountView(root, TransactionListsView, context), contacts: (root: HTMLElement, context: ContactsContext) => mountView(root, ContactsView, context), expenseDetail: (root: HTMLElement, context: ExpenseDetailContext) => mountView(root, ExpenseDetail, context)};
declare global {var AutonomoViews: typeof views;}
globalThis.AutonomoViews = views;
