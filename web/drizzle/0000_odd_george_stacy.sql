CREATE TABLE `workspace_records` (
	`workspace` text NOT NULL,
	`kind` text NOT NULL,
	`id` text NOT NULL,
	`payload` text NOT NULL,
	PRIMARY KEY(`workspace`, `kind`, `id`)
);
--> statement-breakpoint
CREATE TABLE `workspace_revisions` (
	`workspace` text PRIMARY KEY NOT NULL,
	`revision` integer DEFAULT 0 NOT NULL
);
