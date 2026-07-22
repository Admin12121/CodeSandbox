-- CyberRange Organization Tenant Database Schema
-- Target: MySQL 8.0+
-- Purpose: Per-organization database for cybersecurity workspace data.
-- Boundary:
--   - Master DB owns global users, sessions, billing, platform RBAC, organization registry, storage registry.
--   - This organization DB owns organization-local members, RBAC, labs, cases, artifacts, telemetry, dynamic analysis, detections, timeline, reports, realtime collaboration.
--   - master_user_id values reference master.users.id at application/service layer. Cross-database foreign keys are intentionally not used.

SET NAMES utf8mb4;
SET time_zone = '+00:00';
SET foreign_key_checks = 0;

CREATE DATABASE IF NOT EXISTS `cyberrange_org_template`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE `cyberrange_org_template`;

-- ============================================================
-- 0. SCHEMA META / MIGRATIONS / INDEX DOCUMENTATION
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_meta (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  schema_name VARCHAR(120) NOT NULL,
  schema_version VARCHAR(80) NOT NULL,
  description TEXT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_schema_meta_name (schema_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS schema_migrations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  version VARCHAR(120) NOT NULL,
  name VARCHAR(255) NOT NULL,
  checksum CHAR(64) NULL,
  applied_by VARCHAR(160) NULL,
  applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  execution_ms INT UNSIGNED NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_schema_migrations_version (version),
  KEY idx_schema_migrations_applied_at (applied_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS meta_indexes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  table_name VARCHAR(128) NOT NULL,
  index_name VARCHAR(128) NOT NULL,
  columns_csv VARCHAR(512) NOT NULL,
  index_type ENUM('primary','unique','normal','fulltext','spatial') NOT NULL DEFAULT 'normal',
  purpose VARCHAR(500) NOT NULL,
  query_pattern VARCHAR(700) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_meta_indexes_table_index (table_name, index_name),
  KEY idx_meta_indexes_table (table_name),
  FULLTEXT KEY ft_meta_indexes_purpose (purpose, query_pattern)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO schema_meta (schema_name, schema_version, description)
VALUES (
  'cyberrange_organization_tenant',
  '2026.06.20.001',
  'Per-organization tenant schema for automated cybersecurity workspace, labs, cases, telemetry, malware analysis, detections, timeline, reports, RBAC, realtime collaboration.'
)
ON DUPLICATE KEY UPDATE
  schema_version = VALUES(schema_version),
  description = VALUES(description),
  updated_at = CURRENT_TIMESTAMP(6);

-- ============================================================
-- 1. ORGANIZATION LOCAL CONFIGURATION
-- ============================================================

CREATE TABLE organization_profile (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  master_organization_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(180) NOT NULL,
  slug VARCHAR(180) NOT NULL,
  logo_url VARCHAR(1000) NULL,
  timezone VARCHAR(80) NOT NULL DEFAULT 'UTC',
  locale VARCHAR(32) NOT NULL DEFAULT 'en',
  status ENUM('provisioning','active','suspended','archived','deleted') NOT NULL DEFAULT 'active',
  settings_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_profile_public_id (public_id),
  UNIQUE KEY uq_organization_profile_master_org (master_organization_id),
  UNIQUE KEY uq_organization_profile_slug (slug),
  KEY idx_organization_profile_status (status),
  KEY idx_organization_profile_deleted_at (deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  setting_key VARCHAR(160) NOT NULL,
  value_json JSON NOT NULL,
  is_public BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by_member_id BIGINT UNSIGNED NULL,
  updated_by_master_user_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_settings_key (setting_key),
  KEY idx_organization_settings_public (is_public),
  KEY idx_organization_settings_updated_by (updated_by_member_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 2. ORGANIZATION MEMBERS
-- ============================================================

CREATE TABLE organization_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  master_user_id BIGINT UNSIGNED NOT NULL,
  display_name VARCHAR(180) NOT NULL,
  username_snapshot VARCHAR(120) NULL,
  email_snapshot VARCHAR(255) NOT NULL,
  avatar_url VARCHAR(1000) NULL,
  status ENUM('invited','active','suspended','removed') NOT NULL DEFAULT 'active',
  member_type ENUM('owner','admin','member','guest','external') NOT NULL DEFAULT 'member',
  joined_at TIMESTAMP(6) NULL,
  last_seen_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_members_public_id (public_id),
  UNIQUE KEY uq_organization_members_master_user (master_user_id),
  KEY idx_organization_members_status_type (status, member_type),
  KEY idx_organization_members_email (email_snapshot),
  KEY idx_organization_members_last_seen (last_seen_at),
  KEY idx_organization_members_deleted_at (deleted_at),
  FULLTEXT KEY ft_organization_members_search (display_name, username_snapshot, email_snapshot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE organization_settings
  ADD CONSTRAINT fk_org_settings_updated_by_member
  FOREIGN KEY (updated_by_member_id) REFERENCES organization_members(id)
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE member_presence (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  member_id BIGINT UNSIGNED NOT NULL,
  presence_status ENUM('online','idle','busy','offline') NOT NULL DEFAULT 'offline',
  active_room_id BIGINT UNSIGNED NULL,
  last_heartbeat_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_member_presence_member (member_id),
  KEY idx_member_presence_status (presence_status, last_heartbeat_at),
  CONSTRAINT fk_member_presence_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 3. ORGANIZATION RBAC
-- ============================================================

CREATE TABLE organization_permissions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  permission_key VARCHAR(180) NOT NULL,
  resource VARCHAR(100) NOT NULL,
  action VARCHAR(80) NOT NULL,
  description VARCHAR(500) NULL,
  is_system BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_org_permissions_key (permission_key),
  KEY idx_org_permissions_resource_action (resource, action),
  KEY idx_org_permissions_system (is_system)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(140) NOT NULL,
  description VARCHAR(500) NULL,
  color VARCHAR(32) NULL,
  position INT NOT NULL DEFAULT 0,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  is_system_role BOOLEAN NOT NULL DEFAULT FALSE,
  is_mutable BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_org_roles_public_id (public_id),
  UNIQUE KEY uq_org_roles_slug (slug),
  KEY idx_org_roles_default_position (is_default, position),
  KEY idx_org_roles_system_mutable (is_system_role, is_mutable),
  KEY idx_org_roles_deleted_at (deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_role_permissions (
  role_id BIGINT UNSIGNED NOT NULL,
  permission_id BIGINT UNSIGNED NOT NULL,
  allowed BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (role_id, permission_id),
  KEY idx_org_role_permissions_permission (permission_id),
  CONSTRAINT fk_org_role_permissions_role
    FOREIGN KEY (role_id) REFERENCES organization_roles(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_org_role_permissions_permission
    FOREIGN KEY (permission_id) REFERENCES organization_permissions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_member_roles (
  member_id BIGINT UNSIGNED NOT NULL,
  role_id BIGINT UNSIGNED NOT NULL,
  assigned_by_member_id BIGINT UNSIGNED NULL,
  assigned_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (member_id, role_id),
  KEY idx_org_member_roles_role (role_id),
  KEY idx_org_member_roles_assigned_by (assigned_by_member_id),
  CONSTRAINT fk_org_member_roles_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_org_member_roles_role
    FOREIGN KEY (role_id) REFERENCES organization_roles(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_org_member_roles_assigned_by
    FOREIGN KEY (assigned_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 4. TAGS / SHARED LABELING
-- ============================================================

CREATE TABLE tags (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(140) NOT NULL,
  color VARCHAR(32) NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_tags_slug (slug),
  KEY idx_tags_created_by (created_by_member_id),
  FULLTEXT KEY ft_tags_name (name),
  CONSTRAINT fk_tags_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 5. LABS / CYBER RANGE
-- ============================================================

CREATE TABLE labs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(220) NOT NULL,
  slug VARCHAR(240) NOT NULL,
  description TEXT NULL,
  difficulty ENUM('beginner','easy','medium','hard','insane') NOT NULL DEFAULT 'beginner',
  category ENUM('web','forensics','malware','reverse','pwn','crypto','ad','cloud','network','blue_team','custom') NOT NULL DEFAULT 'custom',
  visibility ENUM('private','organization','shared') NOT NULL DEFAULT 'organization',
  status ENUM('draft','published','archived','deleted') NOT NULL DEFAULT 'draft',
  current_version_id BIGINT UNSIGNED NULL,
  estimated_minutes INT UNSIGNED NULL,
  tags_json JSON NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_labs_public_id (public_id),
  UNIQUE KEY uq_labs_slug (slug),
  KEY idx_labs_status_visibility (status, visibility),
  KEY idx_labs_category_difficulty (category, difficulty),
  KEY idx_labs_created_by_status (created_by_member_id, status),
  KEY idx_labs_deleted_at (deleted_at),
  FULLTEXT KEY ft_labs_search (title, description),
  CONSTRAINT fk_labs_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  lab_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(80) NOT NULL,
  changelog TEXT NULL,
  definition_json JSON NOT NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  published_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_versions_public_id (public_id),
  UNIQUE KEY uq_lab_versions_lab_version (lab_id, version),
  KEY idx_lab_versions_lab_published (lab_id, published_at),
  KEY idx_lab_versions_created_by (created_by_member_id),
  CONSTRAINT fk_lab_versions_lab
    FOREIGN KEY (lab_id) REFERENCES labs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_versions_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE labs
  ADD CONSTRAINT fk_labs_current_version
  FOREIGN KEY (current_version_id) REFERENCES lab_versions(id)
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE lab_machines (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NOT NULL,
  machine_role ENUM('attacker','victim','server','domain_controller','sensor','router','custom') NOT NULL DEFAULT 'custom',
  image_ref VARCHAR(500) NOT NULL,
  os_type ENUM('linux','windows','android','bsd','other') NOT NULL DEFAULT 'linux',
  cpu_cores SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  ram_mb INT UNSIGNED NOT NULL DEFAULT 1024,
  disk_gb INT UNSIGNED NOT NULL DEFAULT 10,
  startup_script MEDIUMTEXT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_machines_version_name (lab_version_id, name),
  KEY idx_lab_machines_role_os (machine_role, os_type),
  CONSTRAINT fk_lab_machines_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_networks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NOT NULL,
  cidr VARCHAR(64) NOT NULL,
  isolation_mode ENUM('isolated','internet_blocked','controlled_egress','internet_allowed') NOT NULL DEFAULT 'isolated',
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_networks_version_name (lab_version_id, name),
  KEY idx_lab_networks_isolation (isolation_mode),
  CONSTRAINT fk_lab_networks_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_machine_networks (
  machine_id BIGINT UNSIGNED NOT NULL,
  network_id BIGINT UNSIGNED NOT NULL,
  ip_address VARCHAR(64) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (machine_id, network_id),
  KEY idx_lab_machine_networks_network (network_id),
  KEY idx_lab_machine_networks_ip (ip_address),
  CONSTRAINT fk_lab_machine_networks_machine
    FOREIGN KEY (machine_id) REFERENCES lab_machines(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_machine_networks_network
    FOREIGN KEY (network_id) REFERENCES lab_networks(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_steps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  title VARCHAR(220) NOT NULL,
  body_markdown MEDIUMTEXT NULL,
  step_order INT UNSIGNED NOT NULL DEFAULT 0,
  validation_type ENUM('flag','command','file','event','quiz','manual_review','none') NOT NULL DEFAULT 'none',
  expected_value_hash CHAR(64) NULL,
  points INT UNSIGNED NOT NULL DEFAULT 0,
  hint_markdown TEXT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_steps_public_id (public_id),
  UNIQUE KEY uq_lab_steps_order (lab_version_id, step_order),
  KEY idx_lab_steps_validation (validation_type),
  FULLTEXT KEY ft_lab_steps_text (title, body_markdown, hint_markdown),
  CONSTRAINT fk_lab_steps_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_assets (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  master_storage_object_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(220) NOT NULL,
  asset_type ENUM('file','pcap','memory_dump','disk_image','malware_sample','script','document','archive','other') NOT NULL DEFAULT 'file',
  sha256 CHAR(64) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_lab_assets_version_type (lab_version_id, asset_type),
  KEY idx_lab_assets_storage_object (master_storage_object_id),
  KEY idx_lab_assets_sha256 (sha256),
  CONSTRAINT fk_lab_assets_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_attempts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  lab_id BIGINT UNSIGNED NOT NULL,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NOT NULL,
  status ENUM('queued','running','completed','failed','abandoned','expired') NOT NULL DEFAULT 'queued',
  score INT UNSIGNED NOT NULL DEFAULT 0,
  max_score INT UNSIGNED NOT NULL DEFAULT 0,
  started_at TIMESTAMP(6) NULL,
  completed_at TIMESTAMP(6) NULL,
  expires_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_attempts_public_id (public_id),
  KEY idx_lab_attempts_member_status (member_id, status),
  KEY idx_lab_attempts_lab_status (lab_id, status),
  KEY idx_lab_attempts_expires (status, expires_at),
  CONSTRAINT fk_lab_attempts_lab
    FOREIGN KEY (lab_id) REFERENCES labs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_attempts_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_lab_attempts_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_step_submissions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_attempt_id BIGINT UNSIGNED NOT NULL,
  lab_step_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NOT NULL,
  submitted_value_hash CHAR(64) NULL,
  submitted_text TEXT NULL,
  is_correct BOOLEAN NOT NULL DEFAULT FALSE,
  points_awarded INT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_lab_step_submissions_attempt_step (lab_attempt_id, lab_step_id),
  KEY idx_lab_step_submissions_member (member_id, created_at),
  CONSTRAINT fk_lab_step_submissions_attempt
    FOREIGN KEY (lab_attempt_id) REFERENCES lab_attempts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_step_submissions_step
    FOREIGN KEY (lab_step_id) REFERENCES lab_steps(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_step_submissions_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lab_tags (
  lab_id BIGINT UNSIGNED NOT NULL,
  tag_id BIGINT UNSIGNED NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (lab_id, tag_id),
  KEY idx_lab_tags_tag (tag_id),
  CONSTRAINT fk_lab_tags_lab
    FOREIGN KEY (lab_id) REFERENCES labs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_tags_tag
    FOREIGN KEY (tag_id) REFERENCES tags(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 6. CASES / INVESTIGATION WORKSPACE
-- ============================================================

CREATE TABLE cases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(260) NOT NULL,
  slug VARCHAR(280) NOT NULL,
  description TEXT NULL,
  case_type ENUM('malware','forensic','incident','lab','training','reverse','network','custom') NOT NULL DEFAULT 'custom',
  severity ENUM('info','low','medium','high','critical') NOT NULL DEFAULT 'low',
  status ENUM('open','triage','investigating','contained','resolved','archived','deleted') NOT NULL DEFAULT 'open',
  priority TINYINT UNSIGNED NOT NULL DEFAULT 3,
  tags_json JSON NULL,
  metadata_json JSON NULL,
  opened_at TIMESTAMP(6) NULL,
  closed_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cases_public_id (public_id),
  UNIQUE KEY uq_cases_slug (slug),
  KEY idx_cases_status_severity (status, severity),
  KEY idx_cases_type_status (case_type, status),
  KEY idx_cases_created_by_status (created_by_member_id, status),
  KEY idx_cases_priority_status (priority, status),
  KEY idx_cases_opened_at (opened_at),
  KEY idx_cases_deleted_at (deleted_at),
  FULLTEXT KEY ft_cases_search (title, description),
  CONSTRAINT fk_cases_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE case_members (
  case_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NOT NULL,
  case_role ENUM('owner','lead','investigator','analyst','viewer') NOT NULL DEFAULT 'analyst',
  added_by_member_id BIGINT UNSIGNED NULL,
  added_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (case_id, member_id),
  KEY idx_case_members_member (member_id, case_role),
  KEY idx_case_members_added_by (added_by_member_id),
  CONSTRAINT fk_case_members_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_members_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_members_added_by
    FOREIGN KEY (added_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE case_notes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  author_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(240) NULL,
  body_markdown MEDIUMTEXT NOT NULL,
  note_type ENUM('note','finding','todo','hypothesis','timeline','report_section') NOT NULL DEFAULT 'note',
  pinned BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_case_notes_public_id (public_id),
  KEY idx_case_notes_case_created (case_id, created_at),
  KEY idx_case_notes_author (author_member_id, created_at),
  KEY idx_case_notes_type_pinned (note_type, pinned),
  KEY idx_case_notes_deleted_at (deleted_at),
  FULLTEXT KEY ft_case_notes_search (title, body_markdown),
  CONSTRAINT fk_case_notes_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_notes_author
    FOREIGN KEY (author_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE case_tasks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  assigned_to_member_id BIGINT UNSIGNED NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(240) NOT NULL,
  description TEXT NULL,
  status ENUM('todo','doing','blocked','done','cancelled') NOT NULL DEFAULT 'todo',
  priority TINYINT UNSIGNED NOT NULL DEFAULT 3,
  due_at TIMESTAMP(6) NULL,
  completed_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_case_tasks_public_id (public_id),
  KEY idx_case_tasks_case_status (case_id, status),
  KEY idx_case_tasks_assigned_status (assigned_to_member_id, status),
  KEY idx_case_tasks_due (due_at, status),
  CONSTRAINT fk_case_tasks_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_tasks_assigned_to
    FOREIGN KEY (assigned_to_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_case_tasks_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE case_tags (
  case_id BIGINT UNSIGNED NOT NULL,
  tag_id BIGINT UNSIGNED NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (case_id, tag_id),
  KEY idx_case_tags_tag (tag_id),
  CONSTRAINT fk_case_tags_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_tags_tag
    FOREIGN KEY (tag_id) REFERENCES tags(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 7. CANVAS NOTES / ERASER.IO STYLE WORKSPACE
-- ============================================================

CREATE TABLE canvas_boards (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  lab_attempt_id BIGINT UNSIGNED NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(240) NOT NULL,
  board_type ENUM('case','lab','sandbox','simulation','general') NOT NULL DEFAULT 'case',
  viewport_json JSON NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_canvas_boards_public_id (public_id),
  KEY idx_canvas_boards_case (case_id),
  KEY idx_canvas_boards_lab_attempt (lab_attempt_id),
  KEY idx_canvas_boards_created_by (created_by_member_id),
  KEY idx_canvas_boards_type (board_type),
  KEY idx_canvas_boards_deleted_at (deleted_at),
  FULLTEXT KEY ft_canvas_boards_title (title),
  CONSTRAINT fk_canvas_boards_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_boards_lab_attempt
    FOREIGN KEY (lab_attempt_id) REFERENCES lab_attempts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_boards_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE canvas_nodes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  board_id BIGINT UNSIGNED NOT NULL,
  node_type ENUM('text','image','artifact','process','network','note','finding','ioc','timeline_event','shape','code') NOT NULL DEFAULT 'text',
  title VARCHAR(240) NULL,
  content_json JSON NOT NULL,
  position_x DECIMAL(12,2) NOT NULL DEFAULT 0,
  position_y DECIMAL(12,2) NOT NULL DEFAULT 0,
  width DECIMAL(12,2) NOT NULL DEFAULT 320,
  height DECIMAL(12,2) NOT NULL DEFAULT 180,
  style_json JSON NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_canvas_nodes_public_id (public_id),
  KEY idx_canvas_nodes_board_type (board_id, node_type),
  KEY idx_canvas_nodes_created_by (created_by_member_id),
  CONSTRAINT fk_canvas_nodes_board
    FOREIGN KEY (board_id) REFERENCES canvas_boards(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_nodes_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE canvas_edges (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  board_id BIGINT UNSIGNED NOT NULL,
  source_node_id BIGINT UNSIGNED NOT NULL,
  target_node_id BIGINT UNSIGNED NOT NULL,
  label VARCHAR(200) NULL,
  edge_type ENUM('relation','causes','connects','downloads','executes','spawns','writes','reads','indicates') NOT NULL DEFAULT 'relation',
  style_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_canvas_edges_public_id (public_id),
  KEY idx_canvas_edges_board (board_id),
  KEY idx_canvas_edges_source (source_node_id),
  KEY idx_canvas_edges_target (target_node_id),
  CONSTRAINT fk_canvas_edges_board
    FOREIGN KEY (board_id) REFERENCES canvas_boards(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_edges_source
    FOREIGN KEY (source_node_id) REFERENCES canvas_nodes(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_edges_target
    FOREIGN KEY (target_node_id) REFERENCES canvas_nodes(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE canvas_revisions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  board_id BIGINT UNSIGNED NOT NULL,
  changed_by_member_id BIGINT UNSIGNED NULL,
  change_type VARCHAR(120) NOT NULL,
  patch_json JSON NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_canvas_revisions_board_created (board_id, created_at),
  KEY idx_canvas_revisions_changed_by (changed_by_member_id, created_at),
  CONSTRAINT fk_canvas_revisions_board
    FOREIGN KEY (board_id) REFERENCES canvas_boards(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_canvas_revisions_changed_by
    FOREIGN KEY (changed_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 8. ARTIFACTS / EVIDENCE
-- ============================================================

CREATE TABLE artifacts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  uploaded_by_member_id BIGINT UNSIGNED NULL,
  master_storage_object_id BIGINT UNSIGNED NOT NULL,
  original_filename VARCHAR(500) NOT NULL,
  artifact_type ENUM('exe','elf','mach_o','apk','ipa','pcap','memory_dump','disk_image','log','evtx','registry_hive','archive','script','document','image','unknown') NOT NULL DEFAULT 'unknown',
  mime_type VARCHAR(255) NULL,
  file_size_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sha256 CHAR(64) NULL,
  md5 CHAR(32) NULL,
  ssdeep VARCHAR(255) NULL,
  status ENUM('uploaded','queued','analyzing','clean','suspicious','malicious','failed','archived','deleted') NOT NULL DEFAULT 'uploaded',
  verdict ENUM('unknown','clean','suspicious','malicious') NOT NULL DEFAULT 'unknown',
  description TEXT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_artifacts_public_id (public_id),
  KEY idx_artifacts_case_status (case_id, status),
  KEY idx_artifacts_type_status (artifact_type, status),
  KEY idx_artifacts_sha256 (sha256),
  KEY idx_artifacts_md5 (md5),
  KEY idx_artifacts_master_storage (master_storage_object_id),
  KEY idx_artifacts_uploaded_by (uploaded_by_member_id, created_at),
  KEY idx_artifacts_verdict (verdict, created_at),
  KEY idx_artifacts_deleted_at (deleted_at),
  FULLTEXT KEY ft_artifacts_search (original_filename, description),
  CONSTRAINT fk_artifacts_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_artifacts_uploaded_by
    FOREIGN KEY (uploaded_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE artifact_tags (
  artifact_id BIGINT UNSIGNED NOT NULL,
  tag_id BIGINT UNSIGNED NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (artifact_id, tag_id),
  KEY idx_artifact_tags_tag (tag_id),
  CONSTRAINT fk_artifact_tags_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_artifact_tags_tag
    FOREIGN KEY (tag_id) REFERENCES tags(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE artifact_relationships (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_artifact_id BIGINT UNSIGNED NOT NULL,
  target_artifact_id BIGINT UNSIGNED NOT NULL,
  relationship_type ENUM('extracted_from','contains','dropped_by','downloads','downloaded_by','related_to','derived_from','same_family') NOT NULL DEFAULT 'related_to',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_artifact_relationship_pair (source_artifact_id, target_artifact_id, relationship_type),
  KEY idx_artifact_relationships_target (target_artifact_id),
  KEY idx_artifact_relationships_type (relationship_type, confidence),
  CONSTRAINT fk_artifact_relationships_source
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_artifact_relationships_target
    FOREIGN KEY (target_artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE artifact_comments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  artifact_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  KEY idx_artifact_comments_artifact_created (artifact_id, created_at),
  KEY idx_artifact_comments_member (member_id, created_at),
  CONSTRAINT fk_artifact_comments_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_artifact_comments_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 9. RUNTIME / SANDBOX INSTANCES
-- ============================================================

CREATE TABLE sandbox_instances (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  source_type ENUM('lab_attempt','case_analysis','manual_sandbox','dynamic_analysis','simulation') NOT NULL DEFAULT 'manual_sandbox',
  source_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  status ENUM('queued','provisioning','running','paused','stopping','stopped','failed','expired') NOT NULL DEFAULT 'queued',
  provider ENUM('docker','qemu','firecracker','libvirt','emulator','external') NOT NULL DEFAULT 'docker',
  sandbox_image_ref VARCHAR(500) NOT NULL,
  cpu_cores SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  ram_mb INT UNSIGNED NOT NULL DEFAULT 1024,
  gpu_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  network_mode ENUM('isolated','blocked','simulated_internet','controlled_egress','internet_allowed') NOT NULL DEFAULT 'isolated',
  expires_at TIMESTAMP(6) NULL,
  started_at TIMESTAMP(6) NULL,
  stopped_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sandbox_instances_public_id (public_id),
  KEY idx_sandbox_instances_status_expires (status, expires_at),
  KEY idx_sandbox_instances_created_by_status (created_by_member_id, status),
  KEY idx_sandbox_instances_source (source_type, source_id),
  KEY idx_sandbox_instances_case (case_id, status),
  KEY idx_sandbox_instances_provider_status (provider, status),
  CONSTRAINT fk_sandbox_instances_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_sandbox_instances_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_machines (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_instance_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NOT NULL,
  machine_role ENUM('attacker','victim','server','domain_controller','sensor','router','custom') NOT NULL DEFAULT 'custom',
  provider_machine_id VARCHAR(255) NULL,
  os_type ENUM('linux','windows','android','bsd','other') NOT NULL DEFAULT 'linux',
  ip_address VARCHAR(64) NULL,
  status ENUM('created','running','stopped','failed','deleted') NOT NULL DEFAULT 'created',
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sandbox_machines_instance_name (sandbox_instance_id, name),
  KEY idx_sandbox_machines_provider_id (provider_machine_id),
  KEY idx_sandbox_machines_status (status),
  KEY idx_sandbox_machines_ip (ip_address),
  CONSTRAINT fk_sandbox_machines_instance
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_networks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_instance_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NOT NULL,
  cidr VARCHAR(64) NULL,
  isolation_mode ENUM('isolated','blocked','simulated_internet','controlled_egress','internet_allowed') NOT NULL DEFAULT 'isolated',
  provider_network_id VARCHAR(255) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sandbox_networks_instance_name (sandbox_instance_id, name),
  KEY idx_sandbox_networks_provider_id (provider_network_id),
  KEY idx_sandbox_networks_isolation (isolation_mode),
  CONSTRAINT fk_sandbox_networks_instance
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_ports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_machine_id BIGINT UNSIGNED NOT NULL,
  internal_port INT UNSIGNED NOT NULL,
  external_port INT UNSIGNED NULL,
  protocol ENUM('tcp','udp') NOT NULL DEFAULT 'tcp',
  access_url VARCHAR(1000) NULL,
  is_public BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sandbox_ports_machine_internal (sandbox_machine_id, internal_port, protocol),
  KEY idx_sandbox_ports_external (external_port, protocol),
  KEY idx_sandbox_ports_public (is_public),
  CONSTRAINT fk_sandbox_ports_machine
    FOREIGN KEY (sandbox_machine_id) REFERENCES sandbox_machines(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_credentials (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_machine_id BIGINT UNSIGNED NOT NULL,
  username VARCHAR(160) NOT NULL,
  secret_encrypted TEXT NOT NULL,
  credential_type ENUM('ssh','rdp','vnc','web','api','other') NOT NULL DEFAULT 'ssh',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  KEY idx_sandbox_credentials_machine_type (sandbox_machine_id, credential_type),
  KEY idx_sandbox_credentials_deleted_at (deleted_at),
  CONSTRAINT fk_sandbox_credentials_machine
    FOREIGN KEY (sandbox_machine_id) REFERENCES sandbox_machines(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_instance_id BIGINT UNSIGNED NOT NULL,
  sandbox_machine_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(120) NOT NULL,
  severity ENUM('debug','info','warning','error','critical') NOT NULL DEFAULT 'info',
  message TEXT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_sandbox_events_instance_created (sandbox_instance_id, created_at),
  KEY idx_sandbox_events_machine_created (sandbox_machine_id, created_at),
  KEY idx_sandbox_events_type_created (event_type, created_at),
  CONSTRAINT fk_sandbox_events_instance
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_sandbox_events_machine
    FOREIGN KEY (sandbox_machine_id) REFERENCES sandbox_machines(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sandbox_resource_usage (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sandbox_instance_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NULL,
  cpu_seconds BIGINT UNSIGNED NOT NULL DEFAULT 0,
  gpu_seconds BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ram_mb_peak INT UNSIGNED NOT NULL DEFAULT 0,
  network_bytes_in BIGINT UNSIGNED NOT NULL DEFAULT 0,
  network_bytes_out BIGINT UNSIGNED NOT NULL DEFAULT 0,
  storage_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  measured_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_sandbox_usage_instance_measured (sandbox_instance_id, measured_at),
  KEY idx_sandbox_usage_member_measured (member_id, measured_at),
  CONSTRAINT fk_sandbox_usage_instance
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_sandbox_usage_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 10. AUTO FILE CLASSIFICATION / ANALYSIS ROUTING
-- ============================================================

CREATE TABLE file_classifications (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  artifact_id BIGINT UNSIGNED NOT NULL,
  detected_type ENUM('pe_exe','elf','mach_o','apk','ipa','dex','jar','pcap','evtx','memory_dump','disk_image','archive','script','document','image','unknown') NOT NULL DEFAULT 'unknown',
  mime_type VARCHAR(255) NULL,
  magic_bytes VARCHAR(255) NULL,
  architecture ENUM('x86','x64','arm','arm64','mips','unknown') NOT NULL DEFAULT 'unknown',
  platform ENUM('windows','linux','macos','android','ios','network','memory','disk','unknown') NOT NULL DEFAULT 'unknown',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  detector_name VARCHAR(160) NOT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_file_classifications_artifact (artifact_id, confidence),
  KEY idx_file_classifications_type_platform (detected_type, platform),
  KEY idx_file_classifications_detector (detector_name),
  CONSTRAINT fk_file_classifications_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE analysis_route_decisions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  artifact_id BIGINT UNSIGNED NOT NULL,
  selected_engine_slug VARCHAR(180) NOT NULL,
  reason VARCHAR(700) NULL,
  priority INT UNSIGNED NOT NULL DEFAULT 100,
  auto_started BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_analysis_route_artifact_priority (artifact_id, priority),
  KEY idx_analysis_route_engine (selected_engine_slug),
  KEY idx_analysis_route_auto_started (auto_started, created_at),
  CONSTRAINT fk_analysis_route_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 11. ANALYSIS JOBS
-- ============================================================

CREATE TABLE analysis_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  requested_by_member_id BIGINT UNSIGNED NULL,
  engine_slug VARCHAR(180) NOT NULL,
  engine_version VARCHAR(120) NULL,
  analysis_type ENUM('static','dynamic','forensic','network','ai','detection','report') NOT NULL,
  status ENUM('queued','running','completed','failed','cancelled','timeout') NOT NULL DEFAULT 'queued',
  priority INT UNSIGNED NOT NULL DEFAULT 100,
  progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
  result_summary TEXT NULL,
  result_json JSON NULL,
  error_message TEXT NULL,
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_analysis_jobs_public_id (public_id),
  KEY idx_analysis_jobs_case_status (case_id, status),
  KEY idx_analysis_jobs_artifact_type (artifact_id, analysis_type),
  KEY idx_analysis_jobs_status_priority (status, priority, created_at),
  KEY idx_analysis_jobs_engine_status (engine_slug, status),
  KEY idx_analysis_jobs_requested_by (requested_by_member_id, created_at),
  CONSTRAINT fk_analysis_jobs_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_analysis_jobs_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_analysis_jobs_requested_by
    FOREIGN KEY (requested_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE analysis_job_steps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  analysis_job_id BIGINT UNSIGNED NOT NULL,
  step_name VARCHAR(180) NOT NULL,
  status ENUM('queued','running','completed','failed','skipped') NOT NULL DEFAULT 'queued',
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  log_text MEDIUMTEXT NULL,
  result_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_analysis_job_steps_job_status (analysis_job_id, status),
  KEY idx_analysis_job_steps_name (step_name),
  CONSTRAINT fk_analysis_job_steps_job
    FOREIGN KEY (analysis_job_id) REFERENCES analysis_jobs(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE analysis_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  analysis_job_id BIGINT UNSIGNED NOT NULL,
  level ENUM('debug','info','warning','error','critical') NOT NULL DEFAULT 'info',
  message TEXT NOT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_analysis_logs_job_created (analysis_job_id, created_at),
  KEY idx_analysis_logs_level_created (level, created_at),
  FULLTEXT KEY ft_analysis_logs_message (message),
  CONSTRAINT fk_analysis_logs_job
    FOREIGN KEY (analysis_job_id) REFERENCES analysis_jobs(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 12. DYNAMIC MALWARE ANALYSIS
-- ============================================================

CREATE TABLE dynamic_analysis_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  analysis_job_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NOT NULL,
  sandbox_instance_id BIGINT UNSIGNED NULL,
  os_type ENUM('windows','linux','android','macos','other') NOT NULL DEFAULT 'windows',
  execution_mode ENUM('vm','docker','emulator','hybrid') NOT NULL DEFAULT 'vm',
  network_mode ENUM('blocked','simulated_internet','controlled_egress','internet_allowed') NOT NULL DEFAULT 'simulated_internet',
  status ENUM('queued','running','completed','failed','timeout','cancelled') NOT NULL DEFAULT 'queued',
  verdict ENUM('clean','suspicious','malicious','unknown') NOT NULL DEFAULT 'unknown',
  malware_family VARCHAR(180) NULL,
  malware_type ENUM('rat','c2','ransomware','dropper','stealer','worm','miner','loader','keylogger','banker','unknown') NOT NULL DEFAULT 'unknown',
  behavior_score DECIMAL(6,2) NOT NULL DEFAULT 0.00,
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_dynamic_runs_public_id (public_id),
  UNIQUE KEY uq_dynamic_runs_job (analysis_job_id),
  KEY idx_dynamic_runs_case_status (case_id, status),
  KEY idx_dynamic_runs_artifact (artifact_id),
  KEY idx_dynamic_runs_verdict_type (verdict, malware_type),
  KEY idx_dynamic_runs_sandbox (sandbox_instance_id),
  KEY idx_dynamic_runs_started (started_at),
  CONSTRAINT fk_dynamic_runs_job
    FOREIGN KEY (analysis_job_id) REFERENCES analysis_jobs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_runs_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_runs_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_runs_sandbox
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_process_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  sandbox_machine_id BIGINT UNSIGNED NULL,
  pid INT UNSIGNED NULL,
  ppid INT UNSIGNED NULL,
  process_guid VARCHAR(160) NULL,
  process_name VARCHAR(255) NULL,
  command_line TEXT NULL,
  executable_path VARCHAR(1000) NULL,
  sha256 CHAR(64) NULL,
  event_type ENUM('start','exit','inject','spawn','crash','suspend','resume') NOT NULL DEFAULT 'start',
  event_time TIMESTAMP(6) NOT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_process_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_process_pid_ppid (dynamic_run_id, pid, ppid),
  KEY idx_dynamic_process_name (process_name),
  KEY idx_dynamic_process_sha256 (sha256),
  KEY idx_dynamic_process_machine (sandbox_machine_id),
  FULLTEXT KEY ft_dynamic_process_cmd (process_name, command_line, executable_path),
  CONSTRAINT fk_dynamic_process_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_process_machine
    FOREIGN KEY (sandbox_machine_id) REFERENCES sandbox_machines(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_network_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  protocol ENUM('tcp','udp','icmp','http','https','dns','tls','other') NOT NULL DEFAULT 'tcp',
  src_ip VARCHAR(64) NULL,
  src_port INT UNSIGNED NULL,
  dst_ip VARCHAR(64) NULL,
  dst_port INT UNSIGNED NULL,
  dst_domain VARCHAR(255) NULL,
  url TEXT NULL,
  bytes_sent BIGINT UNSIGNED NOT NULL DEFAULT 0,
  bytes_received BIGINT UNSIGNED NOT NULL DEFAULT 0,
  direction ENUM('inbound','outbound','internal','unknown') NOT NULL DEFAULT 'unknown',
  event_time TIMESTAMP(6) NOT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_network_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_network_process (process_event_id),
  KEY idx_dynamic_network_dst_ip (dst_ip),
  KEY idx_dynamic_network_dst_domain (dst_domain),
  KEY idx_dynamic_network_dst_port (dst_port),
  KEY idx_dynamic_network_protocol_time (protocol, event_time),
  FULLTEXT KEY ft_dynamic_network_url (dst_domain, url),
  CONSTRAINT fk_dynamic_network_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_network_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_dns_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  query_name VARCHAR(255) NOT NULL,
  query_type VARCHAR(32) NOT NULL DEFAULT 'A',
  response_ips_json JSON NULL,
  is_suspicious BOOLEAN NOT NULL DEFAULT FALSE,
  event_time TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_dns_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_dns_query (query_name),
  KEY idx_dynamic_dns_suspicious (is_suspicious, event_time),
  KEY idx_dynamic_dns_process (process_event_id),
  CONSTRAINT fk_dynamic_dns_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_dns_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_file_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_type ENUM('create','modify','delete','rename','encrypt','read','write','permission_change') NOT NULL DEFAULT 'modify',
  file_path VARCHAR(1200) NOT NULL,
  old_file_path VARCHAR(1200) NULL,
  sha256_before CHAR(64) NULL,
  sha256_after CHAR(64) NULL,
  file_size_bytes BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_file_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_file_process (process_event_id),
  KEY idx_dynamic_file_event_type (event_type, event_time),
  KEY idx_dynamic_file_sha_after (sha256_after),
  FULLTEXT KEY ft_dynamic_file_path (file_path, old_file_path),
  CONSTRAINT fk_dynamic_file_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_file_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_registry_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_type ENUM('create','modify','delete','read') NOT NULL DEFAULT 'modify',
  registry_key VARCHAR(1200) NOT NULL,
  registry_value_name VARCHAR(500) NULL,
  registry_value_data TEXT NULL,
  event_time TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_registry_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_registry_process (process_event_id),
  KEY idx_dynamic_registry_event_type (event_type, event_time),
  FULLTEXT KEY ft_dynamic_registry_key (registry_key, registry_value_name, registry_value_data),
  CONSTRAINT fk_dynamic_registry_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_registry_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_persistence_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  persistence_type ENUM('startup_folder','registry_run_key','service','scheduled_task','cron','systemd','launch_agent','browser_extension','unknown') NOT NULL DEFAULT 'unknown',
  target_path VARCHAR(1200) NULL,
  command_line TEXT NULL,
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  event_time TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_persistence_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_persistence_type_confidence (persistence_type, confidence),
  KEY idx_dynamic_persistence_process (process_event_id),
  FULLTEXT KEY ft_dynamic_persistence_cmd (target_path, command_line),
  CONSTRAINT fk_dynamic_persistence_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_persistence_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_ransomware_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  encrypted_file_count INT UNSIGNED NOT NULL DEFAULT 0,
  ransom_note_path VARCHAR(1200) NULL,
  ransom_note_text MEDIUMTEXT NULL,
  extension_added VARCHAR(120) NULL,
  encryption_pattern VARCHAR(255) NULL,
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  event_time TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_ransomware_run_time (dynamic_run_id, event_time),
  KEY idx_dynamic_ransomware_confidence (confidence),
  KEY idx_dynamic_ransomware_process (process_event_id),
  FULLTEXT KEY ft_dynamic_ransomware_note (ransom_note_path, ransom_note_text, encryption_pattern),
  CONSTRAINT fk_dynamic_ransomware_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_ransomware_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dynamic_c2_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dynamic_run_id BIGINT UNSIGNED NOT NULL,
  process_event_id BIGINT UNSIGNED NULL,
  dst_ip VARCHAR(64) NULL,
  dst_domain VARCHAR(255) NULL,
  dst_port INT UNSIGNED NULL,
  protocol VARCHAR(40) NULL,
  beacon_interval_seconds DECIMAL(10,3) NULL,
  beacon_pattern VARCHAR(255) NULL,
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  first_seen_at TIMESTAMP(6) NULL,
  last_seen_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dynamic_c2_run_confidence (dynamic_run_id, confidence),
  KEY idx_dynamic_c2_dst_ip_domain (dst_ip, dst_domain),
  KEY idx_dynamic_c2_dst_port (dst_port),
  KEY idx_dynamic_c2_process (process_event_id),
  KEY idx_dynamic_c2_seen (first_seen_at, last_seen_at),
  CONSTRAINT fk_dynamic_c2_run
    FOREIGN KEY (dynamic_run_id) REFERENCES dynamic_analysis_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dynamic_c2_process
    FOREIGN KEY (process_event_id) REFERENCES dynamic_process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 13. TELEMETRY INGESTION AND NORMALIZED EVENTS
-- ============================================================

CREATE TABLE telemetry_batches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  sandbox_instance_id BIGINT UNSIGNED NULL,
  source_type ENUM('sysmon','evtx','pcap','zeek','suricata','sandbox','velociraptor','edr','linux_audit','windows_event','custom') NOT NULL DEFAULT 'custom',
  uploaded_by_member_id BIGINT UNSIGNED NULL,
  status ENUM('uploaded','queued','parsing','parsed','failed','archived') NOT NULL DEFAULT 'uploaded',
  event_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
  parser_name VARCHAR(160) NULL,
  error_message TEXT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_telemetry_batches_public_id (public_id),
  KEY idx_telemetry_batches_case_status (case_id, status),
  KEY idx_telemetry_batches_artifact (artifact_id),
  KEY idx_telemetry_batches_sandbox (sandbox_instance_id),
  KEY idx_telemetry_batches_source_status (source_type, status),
  CONSTRAINT fk_telemetry_batches_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_telemetry_batches_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_telemetry_batches_sandbox
    FOREIGN KEY (sandbox_instance_id) REFERENCES sandbox_instances(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_telemetry_batches_uploaded_by
    FOREIGN KEY (uploaded_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE telemetry_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_batch_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  event_type VARCHAR(120) NOT NULL,
  source VARCHAR(120) NULL,
  host_name VARCHAR(255) NULL,
  process_name VARCHAR(255) NULL,
  summary TEXT NULL,
  raw_json JSON NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_telemetry_events_case_time (case_id, event_time),
  KEY idx_telemetry_events_batch_time (telemetry_batch_id, event_time),
  KEY idx_telemetry_events_type_time (event_type, event_time),
  KEY idx_telemetry_events_host_time (host_name, event_time),
  KEY idx_telemetry_events_process (process_name),
  FULLTEXT KEY ft_telemetry_events_summary (summary, process_name, host_name),
  CONSTRAINT fk_telemetry_events_batch
    FOREIGN KEY (telemetry_batch_id) REFERENCES telemetry_batches(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_telemetry_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE process_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  host_name VARCHAR(255) NULL,
  pid INT UNSIGNED NULL,
  ppid INT UNSIGNED NULL,
  process_guid VARCHAR(160) NULL,
  parent_process_guid VARCHAR(160) NULL,
  process_name VARCHAR(255) NULL,
  command_line TEXT NULL,
  image_path VARCHAR(1200) NULL,
  user_name VARCHAR(255) NULL,
  sha256 CHAR(64) NULL,
  event_action ENUM('start','stop','inject','access','other') NOT NULL DEFAULT 'start',
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_process_events_case_time (case_id, event_time),
  KEY idx_process_events_guid (process_guid),
  KEY idx_process_events_pid (case_id, host_name, pid, event_time),
  KEY idx_process_events_parent_guid (parent_process_guid),
  KEY idx_process_events_sha256 (sha256),
  FULLTEXT KEY ft_process_events_cmd (process_name, command_line, image_path, user_name),
  CONSTRAINT fk_process_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_process_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE network_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  protocol VARCHAR(40) NULL,
  src_ip VARCHAR(64) NULL,
  src_port INT UNSIGNED NULL,
  dst_ip VARCHAR(64) NULL,
  dst_port INT UNSIGNED NULL,
  dst_domain VARCHAR(255) NULL,
  url TEXT NULL,
  direction ENUM('inbound','outbound','internal','unknown') NOT NULL DEFAULT 'unknown',
  bytes_sent BIGINT UNSIGNED NOT NULL DEFAULT 0,
  bytes_received BIGINT UNSIGNED NOT NULL DEFAULT 0,
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_network_events_case_time (case_id, event_time),
  KEY idx_network_events_process (process_event_id),
  KEY idx_network_events_dst_ip (dst_ip),
  KEY idx_network_events_dst_domain (dst_domain),
  KEY idx_network_events_dst_port (dst_port),
  KEY idx_network_events_protocol (protocol, event_time),
  FULLTEXT KEY ft_network_events_url (dst_domain, url),
  CONSTRAINT fk_network_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_network_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_network_events_process
    FOREIGN KEY (process_event_id) REFERENCES process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dns_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  query_name VARCHAR(255) NOT NULL,
  query_type VARCHAR(32) NOT NULL DEFAULT 'A',
  response_ips_json JSON NULL,
  is_suspicious BOOLEAN NOT NULL DEFAULT FALSE,
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_dns_events_case_time (case_id, event_time),
  KEY idx_dns_events_query (query_name),
  KEY idx_dns_events_suspicious (is_suspicious, event_time),
  KEY idx_dns_events_process (process_event_id),
  CONSTRAINT fk_dns_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_dns_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_dns_events_process
    FOREIGN KEY (process_event_id) REFERENCES process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE file_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  event_action ENUM('create','modify','delete','rename','read','write','encrypt','permission_change','other') NOT NULL DEFAULT 'other',
  file_path VARCHAR(1200) NOT NULL,
  old_file_path VARCHAR(1200) NULL,
  sha256 CHAR(64) NULL,
  file_size_bytes BIGINT UNSIGNED NULL,
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_file_events_case_time (case_id, event_time),
  KEY idx_file_events_process (process_event_id),
  KEY idx_file_events_action_time (event_action, event_time),
  KEY idx_file_events_sha256 (sha256),
  FULLTEXT KEY ft_file_events_path (file_path, old_file_path),
  CONSTRAINT fk_file_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_file_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_file_events_process
    FOREIGN KEY (process_event_id) REFERENCES process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE registry_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  event_action ENUM('create','modify','delete','read','other') NOT NULL DEFAULT 'other',
  registry_key VARCHAR(1200) NOT NULL,
  registry_value_name VARCHAR(500) NULL,
  registry_value_data TEXT NULL,
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_registry_events_case_time (case_id, event_time),
  KEY idx_registry_events_process (process_event_id),
  KEY idx_registry_events_action_time (event_action, event_time),
  FULLTEXT KEY ft_registry_events_key (registry_key, registry_value_name, registry_value_data),
  CONSTRAINT fk_registry_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_registry_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_registry_events_process
    FOREIGN KEY (process_event_id) REFERENCES process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE powershell_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  process_event_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  script_block_id VARCHAR(255) NULL,
  script_text MEDIUMTEXT NULL,
  decoded_text MEDIUMTEXT NULL,
  suspicious_score DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_powershell_events_case_time (case_id, event_time),
  KEY idx_powershell_events_process (process_event_id),
  KEY idx_powershell_events_score (suspicious_score, event_time),
  FULLTEXT KEY ft_powershell_events_script (script_text, decoded_text),
  CONSTRAINT fk_powershell_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_powershell_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_powershell_events_process
    FOREIGN KEY (process_event_id) REFERENCES process_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE auth_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  telemetry_event_id BIGINT UNSIGNED NULL,
  case_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  host_name VARCHAR(255) NULL,
  user_name VARCHAR(255) NULL,
  src_ip VARCHAR(64) NULL,
  auth_type VARCHAR(120) NULL,
  result ENUM('success','failure','unknown') NOT NULL DEFAULT 'unknown',
  raw_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_auth_events_case_time (case_id, event_time),
  KEY idx_auth_events_user_time (user_name, event_time),
  KEY idx_auth_events_src_ip (src_ip),
  KEY idx_auth_events_result (result, event_time),
  CONSTRAINT fk_auth_events_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_auth_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 14. DETECTIONS / RULES / MATCHES
-- ============================================================

CREATE TABLE detection_rules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  name VARCHAR(220) NOT NULL,
  slug VARCHAR(240) NOT NULL,
  rule_type ENUM('yara','sigma','suricata','snort','custom','behavior') NOT NULL DEFAULT 'custom',
  severity ENUM('info','low','medium','high','critical') NOT NULL DEFAULT 'medium',
  status ENUM('draft','active','disabled','archived','deleted') NOT NULL DEFAULT 'draft',
  tags_json JSON NULL,
  description TEXT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_detection_rules_public_id (public_id),
  UNIQUE KEY uq_detection_rules_slug (slug),
  KEY idx_detection_rules_type_status (rule_type, status),
  KEY idx_detection_rules_severity_status (severity, status),
  KEY idx_detection_rules_created_by (created_by_member_id),
  KEY idx_detection_rules_deleted_at (deleted_at),
  FULLTEXT KEY ft_detection_rules_search (name, description),
  CONSTRAINT fk_detection_rules_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE detection_rule_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  detection_rule_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(80) NOT NULL,
  rule_content MEDIUMTEXT NOT NULL,
  rule_metadata_json JSON NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_detection_rule_versions_rule_version (detection_rule_id, version),
  KEY idx_detection_rule_versions_created_by (created_by_member_id),
  FULLTEXT KEY ft_detection_rule_versions_content (rule_content),
  CONSTRAINT fk_detection_rule_versions_rule
    FOREIGN KEY (detection_rule_id) REFERENCES detection_rules(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_detection_rule_versions_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE detection_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  telemetry_batch_id BIGINT UNSIGNED NULL,
  detection_rule_id BIGINT UNSIGNED NOT NULL,
  detection_rule_version_id BIGINT UNSIGNED NOT NULL,
  requested_by_member_id BIGINT UNSIGNED NULL,
  status ENUM('queued','running','completed','failed','cancelled') NOT NULL DEFAULT 'queued',
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  result_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_detection_runs_public_id (public_id),
  KEY idx_detection_runs_case_status (case_id, status),
  KEY idx_detection_runs_artifact (artifact_id),
  KEY idx_detection_runs_batch (telemetry_batch_id),
  KEY idx_detection_runs_rule_status (detection_rule_id, status),
  KEY idx_detection_runs_requested_by (requested_by_member_id, created_at),
  CONSTRAINT fk_detection_runs_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_detection_runs_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_detection_runs_batch
    FOREIGN KEY (telemetry_batch_id) REFERENCES telemetry_batches(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_detection_runs_rule
    FOREIGN KEY (detection_rule_id) REFERENCES detection_rules(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_detection_runs_rule_version
    FOREIGN KEY (detection_rule_version_id) REFERENCES detection_rule_versions(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_detection_runs_requested_by
    FOREIGN KEY (requested_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE detection_matches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  detection_run_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  telemetry_event_id BIGINT UNSIGNED NULL,
  matched_value TEXT NULL,
  severity ENUM('info','low','medium','high','critical') NOT NULL DEFAULT 'medium',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  context_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_detection_matches_run (detection_run_id),
  KEY idx_detection_matches_case_severity (case_id, severity),
  KEY idx_detection_matches_artifact (artifact_id),
  KEY idx_detection_matches_telemetry (telemetry_event_id),
  FULLTEXT KEY ft_detection_matches_value (matched_value),
  CONSTRAINT fk_detection_matches_run
    FOREIGN KEY (detection_run_id) REFERENCES detection_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_detection_matches_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_detection_matches_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_detection_matches_telemetry
    FOREIGN KEY (telemetry_event_id) REFERENCES telemetry_events(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 15. FINDINGS / IOCS
-- ============================================================

CREATE TABLE findings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  artifact_id BIGINT UNSIGNED NULL,
  analysis_job_id BIGINT UNSIGNED NULL,
  detection_match_id BIGINT UNSIGNED NULL,
  title VARCHAR(260) NOT NULL,
  finding_type ENUM('malware','persistence','c2','credential_access','encryption','exfiltration','lateral_movement','defense_evasion','suspicious_behavior','ioc','other') NOT NULL DEFAULT 'other',
  severity ENUM('info','low','medium','high','critical') NOT NULL DEFAULT 'medium',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  description TEXT NULL,
  evidence_json JSON NULL,
  status ENUM('open','accepted','false_positive','resolved','suppressed') NOT NULL DEFAULT 'open',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_findings_public_id (public_id),
  KEY idx_findings_case_severity (case_id, severity),
  KEY idx_findings_case_status (case_id, status),
  KEY idx_findings_artifact (artifact_id),
  KEY idx_findings_analysis_job (analysis_job_id),
  KEY idx_findings_detection_match (detection_match_id),
  KEY idx_findings_type_confidence (finding_type, confidence),
  FULLTEXT KEY ft_findings_search (title, description),
  CONSTRAINT fk_findings_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_findings_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_findings_analysis_job
    FOREIGN KEY (analysis_job_id) REFERENCES analysis_jobs(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_findings_detection_match
    FOREIGN KEY (detection_match_id) REFERENCES detection_matches(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iocs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  artifact_id BIGINT UNSIGNED NULL,
  finding_id BIGINT UNSIGNED NULL,
  value VARCHAR(1200) NOT NULL,
  value_hash CHAR(64) NOT NULL,
  ioc_type ENUM('ip','domain','url','hash_md5','hash_sha1','hash_sha256','email','filepath','registry_key','mutex','user_agent','ja3','other') NOT NULL DEFAULT 'other',
  source_type ENUM('static','dynamic','detection','manual','telemetry','ai') NOT NULL DEFAULT 'manual',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  first_seen_at TIMESTAMP(6) NULL,
  last_seen_at TIMESTAMP(6) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_iocs_public_id (public_id),
  UNIQUE KEY uq_iocs_case_type_hash (case_id, ioc_type, value_hash),
  KEY idx_iocs_case_type (case_id, ioc_type),
  KEY idx_iocs_artifact (artifact_id),
  KEY idx_iocs_finding (finding_id),
  KEY idx_iocs_value_hash (value_hash),
  KEY idx_iocs_seen (first_seen_at, last_seen_at),
  FULLTEXT KEY ft_iocs_value (value),
  CONSTRAINT fk_iocs_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_iocs_artifact
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_iocs_finding
    FOREIGN KEY (finding_id) REFERENCES findings(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 16. TIMELINE / ATTACK REPLAY
-- ============================================================

CREATE TABLE timeline_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  source_type ENUM('telemetry','dynamic','sandbox','finding','detection','manual','lab') NOT NULL DEFAULT 'manual',
  source_id BIGINT UNSIGNED NULL,
  event_time TIMESTAMP(6) NOT NULL,
  title VARCHAR(260) NOT NULL,
  description TEXT NULL,
  event_category ENUM('process','network','dns','file','registry','detection','finding','user','sandbox','auth','powershell','other') NOT NULL DEFAULT 'other',
  severity ENUM('info','low','medium','high','critical') NOT NULL DEFAULT 'info',
  actor VARCHAR(500) NULL,
  target VARCHAR(500) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_timeline_events_public_id (public_id),
  KEY idx_timeline_events_case_time (case_id, event_time),
  KEY idx_timeline_events_category_time (event_category, event_time),
  KEY idx_timeline_events_source (source_type, source_id),
  KEY idx_timeline_events_severity (severity, event_time),
  FULLTEXT KEY ft_timeline_events_search (title, description, actor, target),
  CONSTRAINT fk_timeline_events_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE timeline_edges (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  case_id BIGINT UNSIGNED NOT NULL,
  source_event_id BIGINT UNSIGNED NOT NULL,
  target_event_id BIGINT UNSIGNED NOT NULL,
  relationship_type ENUM('spawned','connected_to','wrote_file','read_file','downloaded','triggered','created_registry','detected_by','related_to','caused') NOT NULL DEFAULT 'related_to',
  confidence DECIMAL(5,2) NOT NULL DEFAULT 0.00,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_timeline_edges_pair_type (source_event_id, target_event_id, relationship_type),
  KEY idx_timeline_edges_case (case_id),
  KEY idx_timeline_edges_source (source_event_id),
  KEY idx_timeline_edges_target (target_event_id),
  KEY idx_timeline_edges_type_confidence (relationship_type, confidence),
  CONSTRAINT fk_timeline_edges_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_timeline_edges_source
    FOREIGN KEY (source_event_id) REFERENCES timeline_events(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_timeline_edges_target
    FOREIGN KEY (target_event_id) REFERENCES timeline_events(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 17. REALTIME COLLABORATION / CHAT / SYNC SIMULATION
-- ============================================================

CREATE TABLE realtime_rooms (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  room_type ENUM('case','lab_attempt','sandbox','simulation','canvas','general') NOT NULL DEFAULT 'general',
  source_id BIGINT UNSIGNED NULL,
  name VARCHAR(220) NOT NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_realtime_rooms_public_id (public_id),
  KEY idx_realtime_rooms_type_source (room_type, source_id),
  KEY idx_realtime_rooms_created_by (created_by_member_id),
  KEY idx_realtime_rooms_deleted_at (deleted_at),
  CONSTRAINT fk_realtime_rooms_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE realtime_room_members (
  room_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NOT NULL,
  joined_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  last_seen_at TIMESTAMP(6) NULL,
  PRIMARY KEY (room_id, member_id),
  KEY idx_realtime_room_members_member (member_id, last_seen_at),
  CONSTRAINT fk_realtime_room_members_room
    FOREIGN KEY (room_id) REFERENCES realtime_rooms(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_realtime_room_members_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE chat_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  room_id BIGINT UNSIGNED NOT NULL,
  sender_member_id BIGINT UNSIGNED NULL,
  message_type ENUM('text','system','artifact','finding','timeline_event','lab_event','command') NOT NULL DEFAULT 'text',
  body TEXT NULL,
  metadata_json JSON NULL,
  edited_at TIMESTAMP(6) NULL,
  deleted_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_chat_messages_public_id (public_id),
  KEY idx_chat_messages_room_created (room_id, created_at),
  KEY idx_chat_messages_sender_created (sender_member_id, created_at),
  KEY idx_chat_messages_type_created (message_type, created_at),
  KEY idx_chat_messages_deleted_at (deleted_at),
  FULLTEXT KEY ft_chat_messages_body (body),
  CONSTRAINT fk_chat_messages_room
    FOREIGN KEY (room_id) REFERENCES realtime_rooms(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_chat_messages_sender
    FOREIGN KEY (sender_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sync_sessions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  room_id BIGINT UNSIGNED NOT NULL,
  leader_member_id BIGINT UNSIGNED NULL,
  source_type ENUM('lab_attempt','sandbox','case','canvas','simulation') NOT NULL DEFAULT 'case',
  source_id BIGINT UNSIGNED NULL,
  status ENUM('active','paused','ended') NOT NULL DEFAULT 'active',
  state_json JSON NULL,
  started_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  ended_at TIMESTAMP(6) NULL,
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sync_sessions_public_id (public_id),
  KEY idx_sync_sessions_room_status (room_id, status),
  KEY idx_sync_sessions_source (source_type, source_id),
  KEY idx_sync_sessions_leader (leader_member_id),
  CONSTRAINT fk_sync_sessions_room
    FOREIGN KEY (room_id) REFERENCES realtime_rooms(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_sync_sessions_leader
    FOREIGN KEY (leader_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sync_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sync_session_id BIGINT UNSIGNED NOT NULL,
  member_id BIGINT UNSIGNED NULL,
  event_type ENUM('cursor','viewport','command','lab_state','note_change','canvas_change','sandbox_state','timeline_seek','chat_state') NOT NULL DEFAULT 'cursor',
  payload_json JSON NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_sync_events_session_created (sync_session_id, created_at),
  KEY idx_sync_events_member_created (member_id, created_at),
  KEY idx_sync_events_type_created (event_type, created_at),
  CONSTRAINT fk_sync_events_session
    FOREIGN KEY (sync_session_id) REFERENCES sync_sessions(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_sync_events_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- update member_presence now that realtime_rooms exists
ALTER TABLE member_presence
  ADD CONSTRAINT fk_member_presence_active_room
  FOREIGN KEY (active_room_id) REFERENCES realtime_rooms(id)
  ON DELETE SET NULL ON UPDATE CASCADE;

-- ============================================================
-- 18. REPORTS / EXPORTS
-- ============================================================

CREATE TABLE report_templates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(220) NOT NULL,
  slug VARCHAR(240) NOT NULL,
  template_type ENUM('incident','malware','forensic','lab','executive','custom') NOT NULL DEFAULT 'custom',
  content_markdown MEDIUMTEXT NOT NULL,
  schema_json JSON NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_by_member_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_report_templates_public_id (public_id),
  UNIQUE KEY uq_report_templates_slug (slug),
  KEY idx_report_templates_type_default (template_type, is_default),
  KEY idx_report_templates_created_by (created_by_member_id),
  FULLTEXT KEY ft_report_templates_search (name, content_markdown),
  CONSTRAINT fk_report_templates_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE case_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  report_template_id BIGINT UNSIGNED NULL,
  created_by_member_id BIGINT UNSIGNED NULL,
  title VARCHAR(260) NOT NULL,
  report_markdown MEDIUMTEXT NULL,
  report_json JSON NULL,
  status ENUM('draft','generating','generated','exported','failed','archived') NOT NULL DEFAULT 'draft',
  exported_object_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_case_reports_public_id (public_id),
  KEY idx_case_reports_case_status (case_id, status),
  KEY idx_case_reports_template (report_template_id),
  KEY idx_case_reports_created_by (created_by_member_id),
  KEY idx_case_reports_exported_object (exported_object_id),
  FULLTEXT KEY ft_case_reports_search (title, report_markdown),
  CONSTRAINT fk_case_reports_case
    FOREIGN KEY (case_id) REFERENCES cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_case_reports_template
    FOREIGN KEY (report_template_id) REFERENCES report_templates(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_case_reports_created_by
    FOREIGN KEY (created_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE report_exports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  case_report_id BIGINT UNSIGNED NOT NULL,
  exported_by_member_id BIGINT UNSIGNED NULL,
  export_type ENUM('pdf','html','markdown','json','docx','odt') NOT NULL DEFAULT 'pdf',
  master_storage_object_id BIGINT UNSIGNED NULL,
  status ENUM('queued','generated','failed','deleted') NOT NULL DEFAULT 'queued',
  error_message TEXT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_report_exports_report_status (case_report_id, status),
  KEY idx_report_exports_exported_by (exported_by_member_id, created_at),
  KEY idx_report_exports_storage (master_storage_object_id),
  CONSTRAINT fk_report_exports_report
    FOREIGN KEY (case_report_id) REFERENCES case_reports(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_report_exports_exported_by
    FOREIGN KEY (exported_by_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 19. ORGANIZATION NOTIFICATIONS / AUDIT / OUTBOX
-- ============================================================

CREATE TABLE organization_notifications (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  member_id BIGINT UNSIGNED NOT NULL,
  notification_type VARCHAR(120) NOT NULL,
  title VARCHAR(260) NOT NULL,
  body TEXT NULL,
  link_url VARCHAR(1000) NULL,
  metadata_json JSON NULL,
  read_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  deleted_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_org_notifications_public_id (public_id),
  KEY idx_org_notifications_member_read (member_id, read_at, created_at),
  KEY idx_org_notifications_type_created (notification_type, created_at),
  KEY idx_org_notifications_deleted_at (deleted_at),
  CONSTRAINT fk_org_notifications_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_audit_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  actor_member_id BIGINT UNSIGNED NULL,
  master_user_id BIGINT UNSIGNED NULL,
  subject_type VARCHAR(120) NOT NULL,
  subject_id BIGINT UNSIGNED NULL,
  action VARCHAR(160) NOT NULL,
  ip_address VARCHAR(64) NULL,
  user_agent VARCHAR(1000) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_org_audit_actor_created (actor_member_id, created_at),
  KEY idx_org_audit_master_user_created (master_user_id, created_at),
  KEY idx_org_audit_subject (subject_type, subject_id, created_at),
  KEY idx_org_audit_action_created (action, created_at),
  CONSTRAINT fk_org_audit_actor
    FOREIGN KEY (actor_member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE organization_outbox_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  aggregate_type VARCHAR(120) NOT NULL,
  aggregate_id BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(160) NOT NULL,
  payload_json JSON NOT NULL,
  status ENUM('pending','processing','sent','failed','discarded') NOT NULL DEFAULT 'pending',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  processed_at TIMESTAMP(6) NULL,
  PRIMARY KEY (id),
  KEY idx_org_outbox_status_next (status, next_attempt_at),
  KEY idx_org_outbox_aggregate (aggregate_type, aggregate_id),
  KEY idx_org_outbox_event_type (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE idempotency_keys (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  key_hash CHAR(64) NOT NULL,
  member_id BIGINT UNSIGNED NULL,
  request_path VARCHAR(500) NOT NULL,
  request_hash CHAR(64) NOT NULL,
  response_status INT UNSIGNED NULL,
  response_body_json JSON NULL,
  expires_at TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_idempotency_keys_hash (key_hash),
  KEY idx_idempotency_keys_member (member_id, created_at),
  KEY idx_idempotency_keys_expires (expires_at),
  CONSTRAINT fk_idempotency_keys_member
    FOREIGN KEY (member_id) REFERENCES organization_members(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================
-- 20. SEED: ORGANIZATION PERMISSIONS
-- ============================================================

INSERT INTO organization_permissions (permission_key, resource, action, description, is_system) VALUES
('org.members.read', 'org.members', 'read', 'View organization members.', TRUE),
('org.members.manage', 'org.members', 'manage', 'Invite, suspend, remove, and manage organization members.', TRUE),
('org.roles.read', 'org.roles', 'read', 'View organization roles and permissions.', TRUE),
('org.roles.manage', 'org.roles', 'manage', 'Create and modify organization roles and permissions.', TRUE),
('org.settings.read', 'org.settings', 'read', 'View organization settings.', TRUE),
('org.settings.manage', 'org.settings', 'manage', 'Modify organization settings.', TRUE),
('labs.read', 'labs', 'read', 'View labs.', TRUE),
('labs.create', 'labs', 'create', 'Create labs.', TRUE),
('labs.update', 'labs', 'update', 'Update labs.', TRUE),
('labs.delete', 'labs', 'delete', 'Delete or archive labs.', TRUE),
('labs.assign', 'labs', 'assign', 'Assign labs to members.', TRUE),
('labs.start', 'labs', 'start', 'Start lab attempts.', TRUE),
('runtime.view', 'runtime', 'view', 'View sandbox/runtime state.', TRUE),
('runtime.start', 'runtime', 'start', 'Start sandbox/runtime instances.', TRUE),
('runtime.stop', 'runtime', 'stop', 'Stop sandbox/runtime instances.', TRUE),
('runtime.manage', 'runtime', 'manage', 'Manage runtime instances and resources.', TRUE),
('cases.read', 'cases', 'read', 'View cases.', TRUE),
('cases.create', 'cases', 'create', 'Create cases.', TRUE),
('cases.update', 'cases', 'update', 'Update cases.', TRUE),
('cases.delete', 'cases', 'delete', 'Delete or archive cases.', TRUE),
('cases.invite', 'cases', 'invite', 'Invite collaborators to cases.', TRUE),
('cases.report', 'cases', 'report', 'Generate and export case reports.', TRUE),
('artifacts.read', 'artifacts', 'read', 'View artifacts and evidence.', TRUE),
('artifacts.upload', 'artifacts', 'upload', 'Upload artifacts and evidence.', TRUE),
('artifacts.download', 'artifacts', 'download', 'Download artifacts and evidence.', TRUE),
('artifacts.delete', 'artifacts', 'delete', 'Delete or archive artifacts.', TRUE),
('analysis.view', 'analysis', 'view', 'View analysis jobs and results.', TRUE),
('analysis.run', 'analysis', 'run', 'Run static, dynamic, forensic, network, and AI analysis.', TRUE),
('analysis.cancel', 'analysis', 'cancel', 'Cancel analysis jobs.', TRUE),
('detections.run', 'detections', 'run', 'Run detection rules.', TRUE),
('detections.manage', 'detections', 'manage', 'Create and manage detection rules.', TRUE),
('timeline.read', 'timeline', 'read', 'View timeline and replay data.', TRUE),
('timeline.manage', 'timeline', 'manage', 'Create, edit, and rebuild timeline data.', TRUE),
('canvas.read', 'canvas', 'read', 'View canvas notes and diagrams.', TRUE),
('canvas.write', 'canvas', 'write', 'Edit canvas notes and diagrams.', TRUE),
('chat.read', 'chat', 'read', 'Read realtime chat messages.', TRUE),
('chat.send', 'chat', 'send', 'Send realtime chat messages.', TRUE),
('chat.manage', 'chat', 'manage', 'Moderate realtime chat rooms.', TRUE),
('simulation.join', 'simulation', 'join', 'Join synchronized simulation sessions.', TRUE),
('simulation.control', 'simulation', 'control', 'Control synchronized simulations.', TRUE),
('simulation.sync', 'simulation', 'sync', 'Synchronize lab, sandbox, canvas, and timeline state.', TRUE),
('audit.read', 'audit', 'read', 'View organization audit logs.', TRUE)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  is_system = VALUES(is_system),
  updated_at = CURRENT_TIMESTAMP(6);

-- ============================================================
-- 21. SEED: DEFAULT ROLES
-- NOTE: public_id placeholders should be replaced by app seed code with real ULIDs.
-- ============================================================

INSERT INTO organization_roles (public_id, name, slug, description, color, position, is_default, is_system_role, is_mutable)
VALUES
('00000000000000000000000001', 'Owner', 'owner', 'Full organization access.', '#f59e0b', 1000, FALSE, TRUE, FALSE),
('00000000000000000000000002', 'Admin', 'admin', 'Administrative access without ownership transfer.', '#ef4444', 900, FALSE, TRUE, TRUE),
('00000000000000000000000003', 'SOC Analyst', 'soc-analyst', 'Can investigate cases, run analysis, manage detections, and view telemetry.', '#3b82f6', 700, FALSE, TRUE, TRUE),
('00000000000000000000000004', 'Lab User', 'lab-user', 'Can access and start assigned labs.', '#22c55e', 500, TRUE, TRUE, TRUE),
('00000000000000000000000005', 'Viewer', 'viewer', 'Read-only organization access.', '#64748b', 100, FALSE, TRUE, TRUE)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  color = VALUES(color),
  position = VALUES(position),
  is_default = VALUES(is_default),
  is_system_role = VALUES(is_system_role),
  is_mutable = VALUES(is_mutable),
  updated_at = CURRENT_TIMESTAMP(6);

-- Owner gets all permissions.
INSERT INTO organization_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, TRUE
FROM organization_roles r
JOIN organization_permissions p
WHERE r.slug = 'owner'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

-- Admin gets all except owner-sensitive settings can still be customized later.
INSERT INTO organization_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, TRUE
FROM organization_roles r
JOIN organization_permissions p
WHERE r.slug = 'admin'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

-- SOC Analyst permissions.
INSERT INTO organization_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, TRUE
FROM organization_roles r
JOIN organization_permissions p
WHERE r.slug = 'soc-analyst'
  AND p.permission_key IN (
    'org.members.read','labs.read','labs.start','runtime.view','runtime.start','runtime.stop',
    'cases.read','cases.create','cases.update','cases.invite','cases.report',
    'artifacts.read','artifacts.upload','artifacts.download','analysis.view','analysis.run','analysis.cancel',
    'detections.run','detections.manage','timeline.read','timeline.manage','canvas.read','canvas.write',
    'chat.read','chat.send','simulation.join','simulation.sync','audit.read'
  )
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

-- Lab User permissions.
INSERT INTO organization_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, TRUE
FROM organization_roles r
JOIN organization_permissions p
WHERE r.slug = 'lab-user'
  AND p.permission_key IN (
    'labs.read','labs.start','runtime.view','runtime.start','runtime.stop',
    'cases.read','cases.create','artifacts.read','artifacts.upload','analysis.view','analysis.run',
    'timeline.read','canvas.read','canvas.write','chat.read','chat.send','simulation.join','simulation.sync'
  )
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

-- Viewer permissions.
INSERT INTO organization_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, TRUE
FROM organization_roles r
JOIN organization_permissions p
WHERE r.slug = 'viewer'
  AND p.permission_key IN (
    'org.members.read','labs.read','runtime.view','cases.read','artifacts.read',
    'analysis.view','timeline.read','canvas.read','chat.read','simulation.join'
  )
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

-- ============================================================
-- 22. META INDEX DOCUMENTATION
-- ============================================================

INSERT INTO meta_indexes (table_name, index_name, columns_csv, index_type, purpose, query_pattern) VALUES
('organization_members','uq_organization_members_master_user','master_user_id','unique','Fast member lookup from global authenticated user after resolving session in master DB.','SELECT * FROM organization_members WHERE master_user_id = ? AND status = active'),
('organization_members','idx_organization_members_status_type','status, member_type','normal','Dashboard filters for active/suspended members and member category.','WHERE status = ? AND member_type = ?'),
('organization_permissions','uq_org_permissions_key','permission_key','unique','Stable Discord-like permission key lookup.','WHERE permission_key IN (...)'),
('organization_role_permissions','PRIMARY','role_id, permission_id','primary','Fast permission expansion from assigned roles.','JOIN organization_role_permissions ON role_id IN (...)'),
('organization_member_roles','PRIMARY','member_id, role_id','primary','Fast role resolution for current member request context.','WHERE member_id = ?'),
('labs','idx_labs_status_visibility','status, visibility','normal','Fast lab listing by published status and visibility.','WHERE status = published AND visibility IN (...)'),
('labs','idx_labs_category_difficulty','category, difficulty','normal','Lab marketplace/catalog filtering.','WHERE category = ? AND difficulty = ?'),
('labs','ft_labs_search','title, description','fulltext','Lab search by title and description.','MATCH(title, description) AGAINST (?)'),
('lab_attempts','idx_lab_attempts_member_status','member_id, status','normal','List active/completed attempts for a member.','WHERE member_id = ? AND status = ?'),
('cases','idx_cases_status_severity','status, severity','normal','SOC/case dashboard filtering.','WHERE status IN (...) AND severity = ?'),
('cases','idx_cases_created_by_status','created_by_member_id, status','normal','My cases page and creator filtering.','WHERE created_by_member_id = ? AND status = ?'),
('cases','ft_cases_search','title, description','fulltext','Investigation case search.','MATCH(title, description) AGAINST (?)'),
('case_members','PRIMARY','case_id, member_id','primary','Fast case collaborator check and access control.','WHERE case_id = ? AND member_id = ?'),
('case_notes','idx_case_notes_case_created','case_id, created_at','normal','Load case notes chronologically.','WHERE case_id = ? ORDER BY created_at DESC'),
('canvas_nodes','idx_canvas_nodes_board_type','board_id, node_type','normal','Load canvas nodes for board and type filters.','WHERE board_id = ?'),
('artifacts','idx_artifacts_case_status','case_id, status','normal','Case evidence list filtering by status.','WHERE case_id = ? AND status = ?'),
('artifacts','idx_artifacts_sha256','sha256','normal','Deduplicate and search evidence by hash.','WHERE sha256 = ?'),
('artifacts','ft_artifacts_search','original_filename, description','fulltext','Search artifacts by file name and analyst description.','MATCH(original_filename, description) AGAINST (?)'),
('sandbox_instances','idx_sandbox_instances_status_expires','status, expires_at','normal','Runtime worker cleanup and expiry scanning.','WHERE status IN (...) AND expires_at < NOW()'),
('sandbox_events','idx_sandbox_events_instance_created','sandbox_instance_id, created_at','normal','Live sandbox event feed.','WHERE sandbox_instance_id = ? ORDER BY created_at'),
('analysis_jobs','idx_analysis_jobs_status_priority','status, priority, created_at','normal','Worker job picking by queue status and priority.','WHERE status = queued ORDER BY priority, created_at'),
('analysis_jobs','idx_analysis_jobs_case_status','case_id, status','normal','Case analysis jobs dashboard.','WHERE case_id = ? AND status = ?'),
('dynamic_analysis_runs','idx_dynamic_runs_verdict_type','verdict, malware_type','normal','Malware analysis dashboard filtering for RAT/C2/ransomware.','WHERE verdict = malicious AND malware_type = ?'),
('dynamic_process_events','idx_dynamic_process_run_time','dynamic_run_id, event_time','normal','Replay process tree in time order.','WHERE dynamic_run_id = ? ORDER BY event_time'),
('dynamic_network_events','idx_dynamic_network_dst_domain','dst_domain','normal','Find callbacks and C2 domains.','WHERE dst_domain = ?'),
('dynamic_dns_events','idx_dynamic_dns_query','query_name','normal','Find DNS indicators.','WHERE query_name = ?'),
('dynamic_file_events','ft_dynamic_file_path','file_path, old_file_path','fulltext','Search changed/encrypted files by path.','MATCH(file_path, old_file_path) AGAINST (?)'),
('dynamic_c2_events','idx_dynamic_c2_dst_ip_domain','dst_ip, dst_domain','normal','Find C2 by IP/domain.','WHERE dst_ip = ? OR dst_domain = ?'),
('telemetry_events','idx_telemetry_events_case_time','case_id, event_time','normal','Core timeline building from normalized telemetry.','WHERE case_id = ? ORDER BY event_time'),
('process_events','idx_process_events_guid','process_guid','normal','Process relationship graph linking.','WHERE process_guid = ?'),
('network_events','idx_network_events_dst_ip','dst_ip','normal','Network IOC search.','WHERE dst_ip = ?'),
('dns_events','idx_dns_events_query','query_name','normal','DNS IOC search.','WHERE query_name = ?'),
('detection_rules','idx_detection_rules_type_status','rule_type, status','normal','Detection rule catalog filtering.','WHERE rule_type = ? AND status = active'),
('detection_matches','idx_detection_matches_case_severity','case_id, severity','normal','Alert dashboard by case and severity.','WHERE case_id = ? AND severity IN (...)'),
('findings','idx_findings_case_severity','case_id, severity','normal','Case findings summary and severity filters.','WHERE case_id = ? ORDER BY severity'),
('iocs','uq_iocs_case_type_hash','case_id, ioc_type, value_hash','unique','Deduplicate IOC values per case and type.','WHERE case_id = ? AND ioc_type = ? AND value_hash = ?'),
('timeline_events','idx_timeline_events_case_time','case_id, event_time','normal','Attack replay timeline ordered by time.','WHERE case_id = ? ORDER BY event_time'),
('timeline_edges','uq_timeline_edges_pair_type','source_event_id, target_event_id, relationship_type','unique','Prevent duplicate attack graph edges.','WHERE source_event_id = ? AND target_event_id = ?'),
('chat_messages','idx_chat_messages_room_created','room_id, created_at','normal','Realtime chat room history pagination.','WHERE room_id = ? ORDER BY created_at DESC'),
('sync_events','idx_sync_events_session_created','sync_session_id, created_at','normal','Realtime synchronization event playback.','WHERE sync_session_id = ? ORDER BY created_at'),
('organization_audit_logs','idx_org_audit_subject','subject_type, subject_id, created_at','normal','Audit trail for any entity.','WHERE subject_type = ? AND subject_id = ? ORDER BY created_at DESC'),
('organization_outbox_events','idx_org_outbox_status_next','status, next_attempt_at','normal','Reliable background event dispatcher.','WHERE status = pending AND next_attempt_at <= NOW()')
ON DUPLICATE KEY UPDATE
  columns_csv = VALUES(columns_csv),
  index_type = VALUES(index_type),
  purpose = VALUES(purpose),
  query_pattern = VALUES(query_pattern);

SET foreign_key_checks = 1;
