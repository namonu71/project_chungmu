import { sqliteTable, text, integer, primaryKey } from 'drizzle-orm/sqlite-core';

export const revisions = sqliteTable('workspace_revisions', {
  workspace: text('workspace').primaryKey(),
  revision: integer('revision').notNull().default(0),
});

export const records = sqliteTable('workspace_records', {
  workspace: text('workspace').notNull(),
  kind: text('kind').notNull(),
  id: text('id').notNull(),
  payload: text('payload').notNull(),
}, table => [primaryKey({columns:[table.workspace,table.kind,table.id]})]);
