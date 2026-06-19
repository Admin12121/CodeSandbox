-- ============================================================================
-- CYBERRANGE / AUTOMATED CYBERSECURITY WORKSPACE
-- MASTER DATABASE SCHEMA
-- MySQL 8.0+ / InnoDB / utf8mb4
-- ============================================================================
-- Purpose:
--   Global control-plane database for:
--   - identity and authentication
--   - personal workspaces
--   - organization registry and organization DB mapping
--   - platform-level RBAC
--   - billing, subscriptions, online payment gateways only
--   - usage limits and quotas
--   - analysis engine catalog and routing rules
--   - runtime worker registry
--   - global storage registry
--   - personal workspace cases/labs/artifacts/analysis data
--   - platform support, settings, legal docs, audit, security events
--
-- Important boundary:
--   Organization case/lab/artifact/collaboration data should live inside each
--   organization's own database. This master DB stores organization identity,
--   membership pointers, billing, and organization_database mapping only.
--
-- Design notes:
--   - id: internal optimized BIGINT primary key.
--   - public_id: app-generated ULID/UUIDv7-style 26-char external identifier.
--   - JSON metadata columns are paired with generated metadata_index where useful.
--   - all payment flows are gateway-based: stripe, esewa, khalti.
--   - no manual/admin-approved payment state is modeled.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS cyberrange_master
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE cyberrange_master;

SET NAMES utf8mb4;
SET time_zone = '+00:00';

-- ============================================================================
-- 1. IDENTITY / AUTHENTICATION
-- ============================================================================

CREATE TABLE users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,

  name VARCHAR(160) NOT NULL,
  email VARCHAR(255) NOT NULL,
  email_verified TINYINT(1) NOT NULL DEFAULT 0,
  username VARCHAR(80) NULL,
  display_username VARCHAR(100) NULL,
  avatar_url VARCHAR(1024) NULL,
  phone VARCHAR(40) NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
  locale VARCHAR(16) NOT NULL DEFAULT 'en',
  bio TEXT NULL,

  status VARCHAR(24) NOT NULL DEFAULT 'active',
  banned_reason TEXT NULL,
  ban_expires_at DATETIME(6) NULL,
  two_factor_enabled TINYINT(1) NOT NULL DEFAULT 0,
  last_login_at DATETIME(6) NULL,

  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,

  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_users_public_id (public_id),
  UNIQUE KEY uq_users_email (email),
  UNIQUE KEY uq_users_username (username),
  KEY idx_users_status_deleted (status, deleted_at),
  KEY idx_users_email_verified (email_verified),
  KEY idx_users_last_login (last_login_at),
  KEY idx_users_metadata_index (metadata_index),
  FULLTEXT KEY ft_users_search (name, email, username),
  CONSTRAINT chk_users_status CHECK (status IN ('active', 'suspended', 'banned', 'deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE auth_accounts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,

  provider VARCHAR(40) NOT NULL,
  provider_account_id VARCHAR(255) NOT NULL,
  password_hash VARCHAR(255) NULL,

  access_token TEXT NULL,
  refresh_token TEXT NULL,
  id_token TEXT NULL,
  token_scope TEXT NULL,
  access_token_expires_at DATETIME(6) NULL,
  refresh_token_expires_at DATETIME(6) NULL,

  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_auth_accounts_public_id (public_id),
  UNIQUE KEY uq_auth_accounts_provider_account (provider, provider_account_id),
  KEY idx_auth_accounts_user_provider (user_id, provider),
  CONSTRAINT fk_auth_accounts_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_auth_accounts_provider CHECK (provider IN ('password', 'google', 'github', 'microsoft', 'passkey'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE sessions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,

  token_hash BINARY(32) NOT NULL,
  ip_address VARBINARY(16) NULL,
  user_agent VARCHAR(512) NULL,
  device_label VARCHAR(160) NULL,

  expires_at DATETIME(6) NOT NULL,
  revoked_at DATETIME(6) NULL,
  revoked_reason VARCHAR(255) NULL,
  last_used_at DATETIME(6) NULL,

  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_sessions_public_id (public_id),
  UNIQUE KEY uq_sessions_token_hash (token_hash),
  KEY idx_sessions_user_expires (user_id, expires_at),
  KEY idx_sessions_revoked_expires (revoked_at, expires_at),
  KEY idx_sessions_last_used (last_used_at),
  CONSTRAINT fk_sessions_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE login_attempts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NULL,
  email VARCHAR(255) NOT NULL,
  ip_address VARBINARY(16) NULL,
  user_agent VARCHAR(512) NULL,
  success TINYINT(1) NOT NULL DEFAULT 0,
  failure_reason VARCHAR(120) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  KEY idx_login_attempts_email_created (email, created_at),
  KEY idx_login_attempts_ip_created (ip_address, created_at),
  KEY idx_login_attempts_user_created (user_id, created_at),
  KEY idx_login_attempts_success_created (success, created_at),
  CONSTRAINT fk_login_attempts_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE verification_tokens (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NULL,
  identifier VARCHAR(255) NOT NULL,
  token_hash BINARY(32) NOT NULL,
  purpose VARCHAR(40) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  used_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_verification_tokens_public_id (public_id),
  UNIQUE KEY uq_verification_tokens_hash (token_hash),
  KEY idx_verification_tokens_identifier_purpose (identifier, purpose),
  KEY idx_verification_tokens_user_purpose (user_id, purpose),
  KEY idx_verification_tokens_expires (expires_at),
  CONSTRAINT fk_verification_tokens_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_verification_tokens_purpose CHECK (purpose IN ('email_verify', 'password_reset', 'login_otp', 'invite_accept'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE two_factor_methods (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  method_type VARCHAR(30) NOT NULL,
  secret_encrypted TEXT NULL,
  backup_codes_encrypted TEXT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 0,
  verified_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_two_factor_methods_public_id (public_id),
  UNIQUE KEY uq_two_factor_methods_user_type (user_id, method_type),
  KEY idx_two_factor_methods_enabled (is_enabled),
  CONSTRAINT fk_two_factor_methods_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_two_factor_methods_type CHECK (method_type IN ('totp', 'recovery_codes'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE passkeys (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NULL,
  credential_id VARCHAR(512) NOT NULL,
  public_key TEXT NOT NULL,
  counter BIGINT UNSIGNED NOT NULL DEFAULT 0,
  device_type VARCHAR(60) NULL,
  backed_up TINYINT(1) NOT NULL DEFAULT 0,
  transports VARCHAR(255) NULL,
  aaguid VARCHAR(80) NULL,
  last_used_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_passkeys_public_id (public_id),
  UNIQUE KEY uq_passkeys_credential_id (credential_id),
  KEY idx_passkeys_user (user_id),
  KEY idx_passkeys_last_used (last_used_at),
  CONSTRAINT fk_passkeys_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE api_keys (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(160) NOT NULL,
  key_hash BINARY(32) NOT NULL,
  prefix VARCHAR(24) NOT NULL,
  scopes_json JSON NULL,
  last_used_at DATETIME(6) NULL,
  expires_at DATETIME(6) NULL,
  revoked_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_api_keys_public_id (public_id),
  UNIQUE KEY uq_api_keys_hash (key_hash),
  KEY idx_api_keys_user_revoked (user_id, revoked_at),
  KEY idx_api_keys_prefix (prefix),
  KEY idx_api_keys_expires (expires_at),
  CONSTRAINT fk_api_keys_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 2. PLATFORM-LEVEL RBAC
-- ============================================================================

CREATE TABLE platform_permissions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  permission_key VARCHAR(160) NOT NULL,
  resource VARCHAR(100) NOT NULL,
  action VARCHAR(60) NOT NULL,
  description VARCHAR(500) NULL,
  is_system TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_platform_permissions_public_id (public_id),
  UNIQUE KEY uq_platform_permissions_key (permission_key),
  KEY idx_platform_permissions_resource_action (resource, action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE platform_roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  description VARCHAR(500) NULL,
  color VARCHAR(40) NULL,
  position INT NOT NULL DEFAULT 0,
  is_default TINYINT(1) NOT NULL DEFAULT 0,
  is_system_role TINYINT(1) NOT NULL DEFAULT 0,
  is_mutable TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_platform_roles_public_id (public_id),
  UNIQUE KEY uq_platform_roles_slug (slug),
  UNIQUE KEY uq_platform_roles_name (name),
  KEY idx_platform_roles_position (position),
  KEY idx_platform_roles_default (is_default)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE platform_role_permissions (
  role_id BIGINT UNSIGNED NOT NULL,
  permission_id BIGINT UNSIGNED NOT NULL,
  allowed TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (role_id, permission_id),
  KEY idx_platform_role_permissions_permission (permission_id),
  CONSTRAINT fk_platform_role_permissions_role
    FOREIGN KEY (role_id) REFERENCES platform_roles(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_platform_role_permissions_permission
    FOREIGN KEY (permission_id) REFERENCES platform_permissions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE platform_user_roles (
  user_id BIGINT UNSIGNED NOT NULL,
  role_id BIGINT UNSIGNED NOT NULL,
  assigned_by BIGINT UNSIGNED NULL,
  assigned_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (user_id, role_id),
  KEY idx_platform_user_roles_role (role_id),
  KEY idx_platform_user_roles_assigned_by (assigned_by),
  CONSTRAINT fk_platform_user_roles_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_platform_user_roles_role
    FOREIGN KEY (role_id) REFERENCES platform_roles(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_platform_user_roles_assigned_by
    FOREIGN KEY (assigned_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 3. PLANS, PRICES, LIMITS
-- ============================================================================

CREATE TABLE plans (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  audience VARCHAR(30) NOT NULL,
  description TEXT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  is_public TINYINT(1) NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_plans_public_id (public_id),
  UNIQUE KEY uq_plans_slug (slug),
  KEY idx_plans_audience_active_public (audience, is_active, is_public),
  KEY idx_plans_sort (sort_order),
  CONSTRAINT chk_plans_audience CHECK (audience IN ('trial', 'personal', 'organization'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE plan_prices (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  plan_id BIGINT UNSIGNED NOT NULL,
  provider VARCHAR(20) NOT NULL,
  provider_price_id VARCHAR(255) NOT NULL,
  currency_code CHAR(3) NOT NULL,
  amount_minor BIGINT UNSIGNED NOT NULL,
  billing_interval VARCHAR(20) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_plan_prices_public_id (public_id),
  UNIQUE KEY uq_plan_prices_provider_price (provider, provider_price_id),
  KEY idx_plan_prices_plan_currency_interval (plan_id, currency_code, billing_interval, is_active),
  CONSTRAINT fk_plan_prices_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_plan_prices_provider CHECK (provider IN ('stripe', 'esewa', 'khalti')),
  CONSTRAINT chk_plan_prices_interval CHECK (billing_interval IN ('monthly', 'yearly'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE plan_limits (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  plan_id BIGINT UNSIGNED NOT NULL,
  limit_key VARCHAR(120) NOT NULL,
  limit_value BIGINT NOT NULL,
  reset_interval VARCHAR(20) NOT NULL DEFAULT 'none',
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_plan_limits_public_id (public_id),
  UNIQUE KEY uq_plan_limits_plan_key (plan_id, limit_key),
  KEY idx_plan_limits_key (limit_key),
  CONSTRAINT fk_plan_limits_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_plan_limits_reset CHECK (reset_interval IN ('none', 'daily', 'weekly', 'monthly'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 4. PERSONAL WORKSPACES
-- ============================================================================

CREATE TABLE personal_workspaces (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  owner_user_id BIGINT UNSIGNED NOT NULL,
  plan_id BIGINT UNSIGNED NULL,
  name VARCHAR(160) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'active',
  storage_used_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_workspaces_public_id (public_id),
  UNIQUE KEY uq_personal_workspaces_slug (slug),
  UNIQUE KEY uq_personal_workspaces_owner (owner_user_id),
  KEY idx_personal_workspaces_plan_status (plan_id, status),
  KEY idx_personal_workspaces_metadata_index (metadata_index),
  CONSTRAINT fk_personal_workspaces_owner
    FOREIGN KEY (owner_user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_workspaces_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_workspaces_status CHECK (status IN ('active', 'suspended', 'deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_workspace_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  invited_by BIGINT UNSIGNED NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'viewer',
  status VARCHAR(24) NOT NULL DEFAULT 'active',
  joined_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_workspace_members_public_id (public_id),
  UNIQUE KEY uq_personal_workspace_members_workspace_user (workspace_id, user_id),
  KEY idx_personal_workspace_members_user_status (user_id, status),
  KEY idx_personal_workspace_members_invited_by (invited_by),
  CONSTRAINT fk_personal_workspace_members_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_workspace_members_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_workspace_members_invited_by
    FOREIGN KEY (invited_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_workspace_members_role CHECK (role IN ('owner', 'collaborator', 'viewer')),
  CONSTRAINT chk_personal_workspace_members_status CHECK (status IN ('invited', 'active', 'removed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_workspace_invitations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  email VARCHAR(255) NOT NULL,
  invited_by BIGINT UNSIGNED NOT NULL,
  token_hash BINARY(32) NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'viewer',
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  expires_at DATETIME(6) NOT NULL,
  accepted_by BIGINT UNSIGNED NULL,
  accepted_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_workspace_invitations_public_id (public_id),
  UNIQUE KEY uq_personal_workspace_invitations_token (token_hash),
  KEY idx_personal_workspace_invitations_workspace_status (workspace_id, status),
  KEY idx_personal_workspace_invitations_email_status (email, status),
  KEY idx_personal_workspace_invitations_expires (expires_at),
  CONSTRAINT fk_personal_workspace_invitations_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_workspace_invitations_invited_by
    FOREIGN KEY (invited_by) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_workspace_invitations_accepted_by
    FOREIGN KEY (accepted_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_workspace_invitations_role CHECK (role IN ('collaborator', 'viewer')),
  CONSTRAINT chk_personal_workspace_invitations_status CHECK (status IN ('pending', 'accepted', 'expired', 'revoked'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 5. ORGANIZATION REGISTRY AND ORGANIZATION DB MAPPING
-- ============================================================================

CREATE TABLE organizations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  owner_user_id BIGINT UNSIGNED NOT NULL,
  plan_id BIGINT UNSIGNED NULL,
  name VARCHAR(180) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  logo_url VARCHAR(1024) NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'provisioning',
  billing_email VARCHAR(255) NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
  locale VARCHAR(16) NOT NULL DEFAULT 'en',
  storage_used_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_organizations_public_id (public_id),
  UNIQUE KEY uq_organizations_slug (slug),
  KEY idx_organizations_owner_status (owner_user_id, status),
  KEY idx_organizations_plan_status (plan_id, status),
  KEY idx_organizations_status_deleted (status, deleted_at),
  KEY idx_organizations_metadata_index (metadata_index),
  FULLTEXT KEY ft_organizations_search (name, slug, billing_email),
  CONSTRAINT fk_organizations_owner
    FOREIGN KEY (owner_user_id) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_organizations_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_organizations_status CHECK (status IN ('provisioning', 'active', 'suspended', 'archived', 'deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE organization_memberships (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  organization_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  member_type VARCHAR(30) NOT NULL DEFAULT 'member',
  status VARCHAR(24) NOT NULL DEFAULT 'active',
  joined_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_memberships_public_id (public_id),
  UNIQUE KEY uq_organization_memberships_org_user (organization_id, user_id),
  KEY idx_organization_memberships_user_status (user_id, status),
  KEY idx_organization_memberships_org_status (organization_id, status),
  CONSTRAINT fk_organization_memberships_org
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_organization_memberships_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_organization_memberships_type CHECK (member_type IN ('owner', 'member', 'external')),
  CONSTRAINT chk_organization_memberships_status CHECK (status IN ('invited', 'active', 'suspended', 'removed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE organization_invitations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  organization_id BIGINT UNSIGNED NOT NULL,
  email VARCHAR(255) NOT NULL,
  invited_by BIGINT UNSIGNED NOT NULL,
  token_hash BINARY(32) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  expires_at DATETIME(6) NOT NULL,
  accepted_by BIGINT UNSIGNED NULL,
  accepted_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_invitations_public_id (public_id),
  UNIQUE KEY uq_organization_invitations_token (token_hash),
  KEY idx_organization_invitations_org_status (organization_id, status),
  KEY idx_organization_invitations_email_status (email, status),
  KEY idx_organization_invitations_expires (expires_at),
  CONSTRAINT fk_organization_invitations_org
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_organization_invitations_invited_by
    FOREIGN KEY (invited_by) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_organization_invitations_accepted_by
    FOREIGN KEY (accepted_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_organization_invitations_status CHECK (status IN ('pending', 'accepted', 'expired', 'revoked'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE organization_databases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  organization_id BIGINT UNSIGNED NOT NULL,
  database_name VARCHAR(160) NOT NULL,
  database_host VARCHAR(255) NOT NULL,
  database_port INT UNSIGNED NOT NULL DEFAULT 3306,
  database_user VARCHAR(160) NOT NULL,
  database_secret_ref VARCHAR(255) NOT NULL,
  migration_version VARCHAR(80) NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'provisioning',
  provisioned_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_databases_public_id (public_id),
  UNIQUE KEY uq_organization_databases_org (organization_id),
  UNIQUE KEY uq_organization_databases_name_host (database_host, database_name),
  KEY idx_organization_databases_status (status),
  CONSTRAINT fk_organization_databases_org
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_organization_databases_status CHECK (status IN ('provisioning', 'ready', 'failed', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE organization_db_provisioning_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  organization_id BIGINT UNSIGNED NOT NULL,
  organization_database_id BIGINT UNSIGNED NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'queued',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_db_jobs_public_id (public_id),
  KEY idx_organization_db_jobs_org_status (organization_id, status),
  KEY idx_organization_db_jobs_db_status (organization_database_id, status),
  KEY idx_organization_db_jobs_created (created_at),
  CONSTRAINT fk_organization_db_jobs_org
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_organization_db_jobs_db
    FOREIGN KEY (organization_database_id) REFERENCES organization_databases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_organization_db_jobs_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE organization_db_migrations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  organization_id BIGINT UNSIGNED NOT NULL,
  organization_database_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(80) NOT NULL,
  name VARCHAR(255) NOT NULL,
  checksum CHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  applied_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_db_migrations_public_id (public_id),
  UNIQUE KEY uq_organization_db_migrations_db_version (organization_database_id, version),
  KEY idx_organization_db_migrations_org_status (organization_id, status),
  CONSTRAINT fk_organization_db_migrations_org
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_organization_db_migrations_db
    FOREIGN KEY (organization_database_id) REFERENCES organization_databases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_organization_db_migrations_status CHECK (status IN ('pending', 'applied', 'failed', 'rolled_back'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 6. BILLING / ONLINE PAYMENT GATEWAYS ONLY
-- ============================================================================

CREATE TABLE subscriptions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  plan_id BIGINT UNSIGNED NOT NULL,

  status VARCHAR(30) NOT NULL DEFAULT 'trialing',
  provider VARCHAR(20) NULL,
  provider_customer_id VARCHAR(255) NULL,
  provider_subscription_id VARCHAR(255) NULL,

  current_period_start DATETIME(6) NOT NULL,
  current_period_end DATETIME(6) NOT NULL,
  cancel_at_period_end TINYINT(1) NOT NULL DEFAULT 0,
  cancelled_at DATETIME(6) NULL,

  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_subscriptions_public_id (public_id),
  UNIQUE KEY uq_subscriptions_subject (subject_type, subject_id),
  UNIQUE KEY uq_subscriptions_provider_subscription (provider, provider_subscription_id),
  KEY idx_subscriptions_plan_status (plan_id, status),
  KEY idx_subscriptions_status_period_end (status, current_period_end),
  KEY idx_subscriptions_provider_customer (provider, provider_customer_id),
  CONSTRAINT fk_subscriptions_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT chk_subscriptions_subject CHECK (subject_type IN ('personal_workspace', 'organization')),
  CONSTRAINT chk_subscriptions_provider CHECK (provider IS NULL OR provider IN ('stripe', 'esewa', 'khalti')),
  CONSTRAINT chk_subscriptions_status CHECK (status IN ('trialing', 'active', 'past_due', 'cancelled', 'expired'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE invoices (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subscription_id BIGINT UNSIGNED NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  provider VARCHAR(20) NOT NULL,
  provider_invoice_id VARCHAR(255) NULL,
  invoice_number VARCHAR(80) NULL,
  currency_code CHAR(3) NOT NULL,
  subtotal_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
  discount_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
  tax_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
  total_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
  status VARCHAR(24) NOT NULL DEFAULT 'open',
  due_at DATETIME(6) NULL,
  paid_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_invoices_public_id (public_id),
  UNIQUE KEY uq_invoices_provider_invoice (provider, provider_invoice_id),
  UNIQUE KEY uq_invoices_number (invoice_number),
  KEY idx_invoices_subscription_status (subscription_id, status),
  KEY idx_invoices_subject_status (subject_type, subject_id, status),
  KEY idx_invoices_status_due (status, due_at),
  KEY idx_invoices_paid (paid_at),
  CONSTRAINT fk_invoices_subscription
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_invoices_provider CHECK (provider IN ('stripe', 'esewa', 'khalti')),
  CONSTRAINT chk_invoices_subject CHECK (subject_type IN ('personal_workspace', 'organization')),
  CONSTRAINT chk_invoices_status CHECK (status IN ('draft', 'open', 'paid', 'failed', 'void', 'refunded'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE payment_transactions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  invoice_id BIGINT UNSIGNED NOT NULL,
  subscription_id BIGINT UNSIGNED NOT NULL,
  provider VARCHAR(20) NOT NULL,
  provider_payment_id VARCHAR(255) NULL,
  provider_reference VARCHAR(255) NULL,
  amount_minor BIGINT UNSIGNED NOT NULL,
  currency_code CHAR(3) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  failure_reason TEXT NULL,
  raw_response_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_payment_transactions_public_id (public_id),
  UNIQUE KEY uq_payment_transactions_provider_payment (provider, provider_payment_id),
  KEY idx_payment_transactions_invoice_status (invoice_id, status),
  KEY idx_payment_transactions_subscription_status (subscription_id, status),
  KEY idx_payment_transactions_provider_reference (provider, provider_reference),
  KEY idx_payment_transactions_created (created_at),
  CONSTRAINT fk_payment_transactions_invoice
    FOREIGN KEY (invoice_id) REFERENCES invoices(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_payment_transactions_subscription
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_payment_transactions_provider CHECK (provider IN ('stripe', 'esewa', 'khalti')),
  CONSTRAINT chk_payment_transactions_status CHECK (status IN ('pending', 'processing', 'succeeded', 'failed', 'cancelled', 'refunded'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE payment_webhook_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  provider VARCHAR(20) NOT NULL,
  event_id VARCHAR(255) NOT NULL,
  event_type VARCHAR(120) NOT NULL,
  payload_json JSON NOT NULL,
  processed_at DATETIME(6) NULL,
  processing_error TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_payment_webhook_events_provider_event (provider, event_id),
  KEY idx_payment_webhook_events_provider_type (provider, event_type),
  KEY idx_payment_webhook_events_processed (processed_at),
  KEY idx_payment_webhook_events_created (created_at),
  CONSTRAINT chk_payment_webhook_events_provider CHECK (provider IN ('stripe', 'esewa', 'khalti'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE coupons (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  code VARCHAR(80) NOT NULL,
  discount_type VARCHAR(20) NOT NULL,
  discount_value BIGINT UNSIGNED NOT NULL,
  currency_code CHAR(3) NULL,
  max_redemptions INT UNSIGNED NULL,
  redeemed_count INT UNSIGNED NOT NULL DEFAULT 0,
  starts_at DATETIME(6) NULL,
  expires_at DATETIME(6) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_coupons_public_id (public_id),
  UNIQUE KEY uq_coupons_code (code),
  KEY idx_coupons_active_dates (is_active, starts_at, expires_at),
  CONSTRAINT chk_coupons_discount_type CHECK (discount_type IN ('fixed', 'percent'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE coupon_redemptions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  coupon_id BIGINT UNSIGNED NOT NULL,
  subscription_id BIGINT UNSIGNED NOT NULL,
  redeemed_by BIGINT UNSIGNED NULL,
  redeemed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_coupon_redemptions_coupon_subscription (coupon_id, subscription_id),
  KEY idx_coupon_redemptions_subscription (subscription_id),
  KEY idx_coupon_redemptions_user (redeemed_by),
  CONSTRAINT fk_coupon_redemptions_coupon
    FOREIGN KEY (coupon_id) REFERENCES coupons(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_coupon_redemptions_subscription
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_coupon_redemptions_user
    FOREIGN KEY (redeemed_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 7. USAGE / QUOTA TRACKING
-- ============================================================================

CREATE TABLE usage_counters (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  plan_id BIGINT UNSIGNED NULL,
  counter_key VARCHAR(120) NOT NULL,
  used_value BIGINT NOT NULL DEFAULT 0,
  limit_value BIGINT NOT NULL DEFAULT 0,
  reset_interval VARCHAR(20) NOT NULL DEFAULT 'none',
  period_start DATETIME(6) NOT NULL,
  period_end DATETIME(6) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_usage_counters_public_id (public_id),
  UNIQUE KEY uq_usage_counters_subject_key_period (subject_type, subject_id, counter_key, period_start, period_end),
  KEY idx_usage_counters_plan_key (plan_id, counter_key),
  KEY idx_usage_counters_period_end (period_end),
  CONSTRAINT fk_usage_counters_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_usage_counters_subject CHECK (subject_type IN ('personal_workspace', 'organization')),
  CONSTRAINT chk_usage_counters_reset CHECK (reset_interval IN ('none', 'daily', 'weekly', 'monthly'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE usage_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(80) NOT NULL,
  quantity BIGINT NOT NULL DEFAULT 1,
  unit VARCHAR(30) NOT NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_usage_events_public_id (public_id),
  KEY idx_usage_events_subject_type_created (subject_type, subject_id, event_type, created_at),
  KEY idx_usage_events_user_created (user_id, created_at),
  KEY idx_usage_events_created (created_at),
  CONSTRAINT fk_usage_events_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_usage_events_subject CHECK (subject_type IN ('personal_workspace', 'organization'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE quota_overrides (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  limit_key VARCHAR(120) NOT NULL,
  override_value BIGINT NOT NULL,
  reason TEXT NULL,
  starts_at DATETIME(6) NULL,
  expires_at DATETIME(6) NULL,
  created_by BIGINT UNSIGNED NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_quota_overrides_public_id (public_id),
  KEY idx_quota_overrides_subject_key (subject_type, subject_id, limit_key),
  KEY idx_quota_overrides_dates (starts_at, expires_at),
  KEY idx_quota_overrides_created_by (created_by),
  CONSTRAINT fk_quota_overrides_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_quota_overrides_subject CHECK (subject_type IN ('personal_workspace', 'organization'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 8. ANALYSIS ENGINE CATALOG AND SANDBOX IMAGE CATALOG
-- ============================================================================

CREATE TABLE analysis_engines (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(160) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  engine_type VARCHAR(40) NOT NULL,
  supported_file_types_json JSON NULL,
  supported_platforms_json JSON NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  requires_vm TINYINT(1) NOT NULL DEFAULT 0,
  requires_gpu TINYINT(1) NOT NULL DEFAULT 0,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_analysis_engines_public_id (public_id),
  UNIQUE KEY uq_analysis_engines_slug (slug),
  KEY idx_analysis_engines_type_active (engine_type, is_active),
  KEY idx_analysis_engines_requires (requires_vm, requires_gpu),
  CONSTRAINT chk_analysis_engines_type CHECK (engine_type IN ('static', 'dynamic', 'forensic', 'network', 'ai', 'reverse_engineering', 'detection'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE analysis_engine_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  analysis_engine_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(80) NOT NULL,
  docker_image VARCHAR(255) NULL,
  vm_image_id BIGINT UNSIGNED NULL,
  config_schema_json JSON NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'active',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_analysis_engine_versions_public_id (public_id),
  UNIQUE KEY uq_analysis_engine_versions_engine_version (analysis_engine_id, version),
  KEY idx_analysis_engine_versions_status (status),
  CONSTRAINT fk_analysis_engine_versions_engine
    FOREIGN KEY (analysis_engine_id) REFERENCES analysis_engines(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_analysis_engine_versions_status CHECK (status IN ('active', 'deprecated', 'disabled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE sandbox_images (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(160) NOT NULL,
  slug VARCHAR(120) NOT NULL,
  os_type VARCHAR(30) NOT NULL,
  image_type VARCHAR(30) NOT NULL,
  image_ref VARCHAR(255) NOT NULL,
  version VARCHAR(80) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_sandbox_images_public_id (public_id),
  UNIQUE KEY uq_sandbox_images_slug_version (slug, version),
  KEY idx_sandbox_images_os_type_active (os_type, image_type, is_active),
  CONSTRAINT chk_sandbox_images_os CHECK (os_type IN ('windows', 'linux', 'android', 'macos')),
  CONSTRAINT chk_sandbox_images_type CHECK (image_type IN ('vm', 'docker', 'firecracker', 'qemu', 'lxc'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE analysis_engine_versions
  ADD CONSTRAINT fk_analysis_engine_versions_vm_image
  FOREIGN KEY (vm_image_id) REFERENCES sandbox_images(id)
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE analysis_routing_rules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(160) NOT NULL,
  priority INT NOT NULL DEFAULT 100,
  file_type VARCHAR(80) NULL,
  mime_type VARCHAR(160) NULL,
  magic_pattern VARCHAR(255) NULL,
  engine_id BIGINT UNSIGNED NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_analysis_routing_rules_public_id (public_id),
  KEY idx_analysis_routing_rules_active_priority (is_active, priority),
  KEY idx_analysis_routing_rules_file_mime (file_type, mime_type),
  KEY idx_analysis_routing_rules_engine (engine_id),
  CONSTRAINT fk_analysis_routing_rules_engine
    FOREIGN KEY (engine_id) REFERENCES analysis_engines(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 9. RUNTIME WORKER REGISTRY
-- ============================================================================

CREATE TABLE runtime_node_pools (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  name VARCHAR(160) NOT NULL,
  pool_type VARCHAR(30) NOT NULL,
  region VARCHAR(80) NULL,
  max_cpu_cores INT UNSIGNED NOT NULL DEFAULT 0,
  max_ram_mb INT UNSIGNED NOT NULL DEFAULT 0,
  max_gpu_count INT UNSIGNED NOT NULL DEFAULT 0,
  status VARCHAR(24) NOT NULL DEFAULT 'active',
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_runtime_node_pools_public_id (public_id),
  UNIQUE KEY uq_runtime_node_pools_name (name),
  KEY idx_runtime_node_pools_type_status (pool_type, status),
  KEY idx_runtime_node_pools_region (region),
  CONSTRAINT chk_runtime_node_pools_type CHECK (pool_type IN ('vm', 'docker', 'gpu', 'cpu', 'mixed')),
  CONSTRAINT chk_runtime_node_pools_status CHECK (status IN ('active', 'maintenance', 'disabled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE runtime_workers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  node_pool_id BIGINT UNSIGNED NULL,
  name VARCHAR(160) NOT NULL,
  worker_type VARCHAR(40) NOT NULL,
  region VARCHAR(80) NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'offline',
  max_parallel_jobs INT UNSIGNED NOT NULL DEFAULT 1,
  current_jobs INT UNSIGNED NOT NULL DEFAULT 0,
  last_heartbeat_at DATETIME(6) NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_runtime_workers_public_id (public_id),
  UNIQUE KEY uq_runtime_workers_name (name),
  KEY idx_runtime_workers_pool_status (node_pool_id, status),
  KEY idx_runtime_workers_type_status (worker_type, status),
  KEY idx_runtime_workers_heartbeat (last_heartbeat_at),
  CONSTRAINT fk_runtime_workers_pool
    FOREIGN KEY (node_pool_id) REFERENCES runtime_node_pools(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_runtime_workers_type CHECK (worker_type IN ('analysis', 'sandbox', 'telemetry', 'ai', 'forensics', 'detection')),
  CONSTRAINT chk_runtime_workers_status CHECK (status IN ('online', 'offline', 'draining', 'unhealthy'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE worker_capabilities (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  worker_id BIGINT UNSIGNED NOT NULL,
  capability_key VARCHAR(120) NOT NULL,
  capability_value VARCHAR(255) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_worker_capabilities_worker_key_value (worker_id, capability_key, capability_value),
  KEY idx_worker_capabilities_key_value (capability_key, capability_value),
  CONSTRAINT fk_worker_capabilities_worker
    FOREIGN KEY (worker_id) REFERENCES runtime_workers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 10. GLOBAL STORAGE REGISTRY
-- ============================================================================

CREATE TABLE storage_objects (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NOT NULL,
  uploaded_by BIGINT UNSIGNED NULL,

  bucket VARCHAR(120) NOT NULL,
  object_key VARCHAR(1024) NOT NULL,
  object_key_hash BINARY(32) NOT NULL, -- SHA-256 of object_key for safe unique indexing
  original_filename VARCHAR(512) NOT NULL,
  content_type VARCHAR(180) NULL,
  file_size_bytes BIGINT UNSIGNED NOT NULL,
  sha256 CHAR(64) NOT NULL,
  md5 CHAR(32) NULL,
  storage_class VARCHAR(40) NOT NULL DEFAULT 'standard',
  status VARCHAR(30) NOT NULL DEFAULT 'uploaded',

  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,

  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_storage_objects_public_id (public_id),
  UNIQUE KEY uq_storage_objects_bucket_key_hash (bucket, object_key_hash),
  KEY idx_storage_objects_object_key_prefix (object_key(191)),
  KEY idx_storage_objects_subject_status (subject_type, subject_id, status),
  KEY idx_storage_objects_uploaded_by_created (uploaded_by, created_at),
  KEY idx_storage_objects_sha256 (sha256),
  KEY idx_storage_objects_content_type (content_type),
  KEY idx_storage_objects_status_created (status, created_at),
  KEY idx_storage_objects_metadata_index (metadata_index),
  CONSTRAINT fk_storage_objects_uploaded_by
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_storage_objects_subject CHECK (subject_type IN ('personal_workspace', 'organization', 'platform')),
  CONSTRAINT chk_storage_objects_status CHECK (status IN ('uploaded', 'quarantined', 'deleted', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 11. GLOBAL LAB TEMPLATE CATALOG
-- ============================================================================

CREATE TABLE lab_templates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  created_by BIGINT UNSIGNED NULL,
  title VARCHAR(200) NOT NULL,
  slug VARCHAR(140) NOT NULL,
  difficulty VARCHAR(30) NOT NULL DEFAULT 'beginner',
  category VARCHAR(80) NOT NULL,
  description TEXT NULL,
  visibility VARCHAR(30) NOT NULL DEFAULT 'public',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_templates_public_id (public_id),
  UNIQUE KEY uq_lab_templates_slug (slug),
  KEY idx_lab_templates_category_difficulty (category, difficulty),
  KEY idx_lab_templates_visibility_active (visibility, is_active),
  KEY idx_lab_templates_created_by (created_by),
  FULLTEXT KEY ft_lab_templates_search (title, description),
  CONSTRAINT fk_lab_templates_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_lab_templates_difficulty CHECK (difficulty IN ('beginner', 'intermediate', 'advanced', 'expert')),
  CONSTRAINT chk_lab_templates_visibility CHECK (visibility IN ('public', 'private', 'unlisted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE lab_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  lab_template_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(80) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'draft',
  config_json JSON NULL,
  published_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_versions_public_id (public_id),
  UNIQUE KEY uq_lab_versions_template_version (lab_template_id, version),
  KEY idx_lab_versions_template_status (lab_template_id, status),
  CONSTRAINT fk_lab_versions_template
    FOREIGN KEY (lab_template_id) REFERENCES lab_templates(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_lab_versions_status CHECK (status IN ('draft', 'published', 'deprecated', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE lab_assets (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  storage_object_id BIGINT UNSIGNED NOT NULL,
  asset_type VARCHAR(60) NOT NULL,
  name VARCHAR(180) NOT NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_lab_assets_public_id (public_id),
  KEY idx_lab_assets_version_type (lab_version_id, asset_type),
  KEY idx_lab_assets_storage (storage_object_id),
  CONSTRAINT fk_lab_assets_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_lab_assets_storage
    FOREIGN KEY (storage_object_id) REFERENCES storage_objects(id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 12. PERSONAL WORKSPACE CONTENT: CASES, CANVAS, ARTIFACTS, ANALYSIS
-- ============================================================================

CREATE TABLE personal_cases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  created_by BIGINT UNSIGNED NULL,
  title VARCHAR(220) NOT NULL,
  slug VARCHAR(160) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'open',
  priority VARCHAR(20) NOT NULL DEFAULT 'normal',
  summary TEXT NULL,
  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  closed_at DATETIME(6) NULL,
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_cases_public_id (public_id),
  UNIQUE KEY uq_personal_cases_workspace_slug (workspace_id, slug),
  KEY idx_personal_cases_workspace_status_updated (workspace_id, status, updated_at),
  KEY idx_personal_cases_created_by (created_by),
  KEY idx_personal_cases_metadata_index (metadata_index),
  FULLTEXT KEY ft_personal_cases_search (title, summary),
  CONSTRAINT fk_personal_cases_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_cases_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_cases_status CHECK (status IN ('open', 'investigating', 'resolved', 'closed', 'archived')),
  CONSTRAINT chk_personal_cases_priority CHECK (priority IN ('low', 'normal', 'high', 'urgent'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_case_members (
  case_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'collaborator',
  added_by BIGINT UNSIGNED NULL,
  added_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (case_id, user_id),
  KEY idx_personal_case_members_user (user_id),
  KEY idx_personal_case_members_added_by (added_by),
  CONSTRAINT fk_personal_case_members_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_members_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_members_added_by
    FOREIGN KEY (added_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_case_members_role CHECK (role IN ('owner', 'collaborator', 'viewer'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_case_notes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  author_user_id BIGINT UNSIGNED NULL,
  title VARCHAR(220) NULL,
  body_markdown MEDIUMTEXT NOT NULL,
  note_type VARCHAR(40) NOT NULL DEFAULT 'note',
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_case_notes_public_id (public_id),
  KEY idx_personal_case_notes_case_created (case_id, created_at),
  KEY idx_personal_case_notes_author (author_user_id),
  FULLTEXT KEY ft_personal_case_notes_search (title, body_markdown),
  CONSTRAINT fk_personal_case_notes_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_notes_author
    FOREIGN KEY (author_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_case_notes_type CHECK (note_type IN ('note', 'finding', 'todo', 'timeline_comment', 'report_draft'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_canvas_boards (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  created_by BIGINT UNSIGNED NULL,
  title VARCHAR(220) NOT NULL,
  board_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_canvas_boards_public_id (public_id),
  KEY idx_personal_canvas_boards_workspace_updated (workspace_id, updated_at),
  KEY idx_personal_canvas_boards_case_updated (case_id, updated_at),
  KEY idx_personal_canvas_boards_created_by (created_by),
  CONSTRAINT fk_personal_canvas_boards_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_canvas_boards_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_canvas_boards_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_canvas_nodes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  board_id BIGINT UNSIGNED NOT NULL,
  node_key VARCHAR(120) NOT NULL,
  node_type VARCHAR(60) NOT NULL,
  x_position DECIMAL(12,2) NOT NULL DEFAULT 0,
  y_position DECIMAL(12,2) NOT NULL DEFAULT 0,
  width DECIMAL(12,2) NULL,
  height DECIMAL(12,2) NULL,
  data_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_canvas_nodes_public_id (public_id),
  UNIQUE KEY uq_personal_canvas_nodes_board_key (board_id, node_key),
  KEY idx_personal_canvas_nodes_board_type (board_id, node_type),
  CONSTRAINT fk_personal_canvas_nodes_board
    FOREIGN KEY (board_id) REFERENCES personal_canvas_boards(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_canvas_edges (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  board_id BIGINT UNSIGNED NOT NULL,
  edge_key VARCHAR(120) NOT NULL,
  source_node_key VARCHAR(120) NOT NULL,
  target_node_key VARCHAR(120) NOT NULL,
  edge_type VARCHAR(60) NOT NULL DEFAULT 'link',
  data_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_canvas_edges_public_id (public_id),
  UNIQUE KEY uq_personal_canvas_edges_board_key (board_id, edge_key),
  KEY idx_personal_canvas_edges_board_source (board_id, source_node_key),
  KEY idx_personal_canvas_edges_board_target (board_id, target_node_key),
  CONSTRAINT fk_personal_canvas_edges_board
    FOREIGN KEY (board_id) REFERENCES personal_canvas_boards(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_artifacts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  storage_object_id BIGINT UNSIGNED NOT NULL,
  uploaded_by BIGINT UNSIGNED NULL,
  artifact_type VARCHAR(80) NOT NULL,
  detected_file_type VARCHAR(100) NULL,
  mime_type VARCHAR(160) NULL,
  sha256 CHAR(64) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'uploaded',
  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_artifacts_public_id (public_id),
  UNIQUE KEY uq_personal_artifacts_storage (storage_object_id),
  KEY idx_personal_artifacts_workspace_status (workspace_id, status),
  KEY idx_personal_artifacts_workspace_type (workspace_id, artifact_type),
  KEY idx_personal_artifacts_sha256 (sha256),
  KEY idx_personal_artifacts_uploaded_by (uploaded_by),
  KEY idx_personal_artifacts_metadata_index (metadata_index),
  CONSTRAINT fk_personal_artifacts_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_artifacts_storage
    FOREIGN KEY (storage_object_id) REFERENCES storage_objects(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_personal_artifacts_uploaded_by
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_artifacts_status CHECK (status IN ('uploaded', 'queued', 'analyzing', 'analyzed', 'failed', 'deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_case_artifacts (
  case_id BIGINT UNSIGNED NOT NULL,
  artifact_id BIGINT UNSIGNED NOT NULL,
  linked_by BIGINT UNSIGNED NULL,
  linked_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (case_id, artifact_id),
  KEY idx_personal_case_artifacts_artifact (artifact_id),
  KEY idx_personal_case_artifacts_linked_by (linked_by),
  CONSTRAINT fk_personal_case_artifacts_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_artifacts_artifact
    FOREIGN KEY (artifact_id) REFERENCES personal_artifacts(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_artifacts_linked_by
    FOREIGN KEY (linked_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_analysis_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  requested_by BIGINT UNSIGNED NULL,
  analysis_engine_id BIGINT UNSIGNED NULL,
  analysis_engine_version_id BIGINT UNSIGNED NULL,

  job_type VARCHAR(60) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'queued',
  priority INT NOT NULL DEFAULT 100,
  classification VARCHAR(80) NULL,
  confidence_score DECIMAL(5,4) NULL,
  summary TEXT NULL,
  config_json JSON NULL,
  result_json JSON NULL,
  error_message TEXT NULL,

  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,

  queued_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_analysis_jobs_public_id (public_id),
  KEY idx_personal_analysis_jobs_workspace_status_priority (workspace_id, status, priority, queued_at),
  KEY idx_personal_analysis_jobs_case_status (case_id, status),
  KEY idx_personal_analysis_jobs_artifact_status (artifact_id, status),
  KEY idx_personal_analysis_jobs_engine_status (analysis_engine_id, status),
  KEY idx_personal_analysis_jobs_requested_by (requested_by),
  KEY idx_personal_analysis_jobs_classification (classification),
  KEY idx_personal_analysis_jobs_metadata_index (metadata_index),
  CONSTRAINT fk_personal_analysis_jobs_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_analysis_jobs_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_analysis_jobs_artifact
    FOREIGN KEY (artifact_id) REFERENCES personal_artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_analysis_jobs_requested_by
    FOREIGN KEY (requested_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_analysis_jobs_engine
    FOREIGN KEY (analysis_engine_id) REFERENCES analysis_engines(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_analysis_jobs_engine_version
    FOREIGN KEY (analysis_engine_version_id) REFERENCES analysis_engine_versions(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_analysis_jobs_type CHECK (job_type IN ('static', 'dynamic_malware', 'reverse_engineering', 'memory_forensics', 'disk_forensics', 'network_forensics', 'ai_summary', 'detection')),
  CONSTRAINT chk_personal_analysis_jobs_status CHECK (status IN ('queued', 'running', 'waiting_observation', 'succeeded', 'failed', 'cancelled', 'timed_out'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_analysis_job_steps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  analysis_job_id BIGINT UNSIGNED NOT NULL,
  step_name VARCHAR(160) NOT NULL,
  step_order INT NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'queued',
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  log_text MEDIUMTEXT NULL,
  result_json JSON NULL,
  error_message TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_analysis_job_steps_public_id (public_id),
  KEY idx_personal_analysis_job_steps_job_order (analysis_job_id, step_order),
  KEY idx_personal_analysis_job_steps_job_status (analysis_job_id, status),
  CONSTRAINT fk_personal_analysis_job_steps_job
    FOREIGN KEY (analysis_job_id) REFERENCES personal_analysis_jobs(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_personal_analysis_job_steps_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_forensic_findings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  analysis_job_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  finding_type VARCHAR(80) NOT NULL,
  severity VARCHAR(20) NOT NULL DEFAULT 'info',
  title VARCHAR(240) NOT NULL,
  description TEXT NULL,
  evidence_json JSON NULL,
  confidence_score DECIMAL(5,4) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_forensic_findings_public_id (public_id),
  KEY idx_personal_forensic_findings_workspace_severity (workspace_id, severity, created_at),
  KEY idx_personal_forensic_findings_case_severity (case_id, severity),
  KEY idx_personal_forensic_findings_job (analysis_job_id),
  KEY idx_personal_forensic_findings_artifact (artifact_id),
  CONSTRAINT fk_personal_forensic_findings_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_forensic_findings_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_forensic_findings_job
    FOREIGN KEY (analysis_job_id) REFERENCES personal_analysis_jobs(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_forensic_findings_artifact
    FOREIGN KEY (artifact_id) REFERENCES personal_artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_forensic_findings_severity CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_iocs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  analysis_job_id BIGINT UNSIGNED NULL,
  artifact_id BIGINT UNSIGNED NULL,
  ioc_type VARCHAR(40) NOT NULL,
  ioc_value VARCHAR(1024) NOT NULL,
  normalized_value VARCHAR(1024) NOT NULL,
  confidence_score DECIMAL(5,4) NULL,
  source VARCHAR(80) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_iocs_public_id (public_id),
  KEY idx_personal_iocs_workspace_type_created (workspace_id, ioc_type, created_at),
  KEY idx_personal_iocs_case_type (case_id, ioc_type),
  KEY idx_personal_iocs_job (analysis_job_id),
  KEY idx_personal_iocs_artifact (artifact_id),
  KEY idx_personal_iocs_normalized_hash (normalized_value(191)),
  CONSTRAINT fk_personal_iocs_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_iocs_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_iocs_job
    FOREIGN KEY (analysis_job_id) REFERENCES personal_analysis_jobs(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_iocs_artifact
    FOREIGN KEY (artifact_id) REFERENCES personal_artifacts(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_iocs_type CHECK (ioc_type IN ('ip', 'domain', 'url', 'hash_md5', 'hash_sha1', 'hash_sha256', 'email', 'registry_key', 'file_path', 'mutex', 'cve', 'yara_match', 'sigma_match'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_sandbox_instances (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  analysis_job_id BIGINT UNSIGNED NULL,
  sandbox_image_id BIGINT UNSIGNED NULL,
  created_by BIGINT UNSIGNED NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'queued',
  runtime_provider VARCHAR(40) NOT NULL DEFAULT 'docker',
  cpu_cores INT UNSIGNED NOT NULL DEFAULT 1,
  ram_mb INT UNSIGNED NOT NULL DEFAULT 1024,
  gpu_enabled TINYINT(1) NOT NULL DEFAULT 0,
  network_mode VARCHAR(40) NOT NULL DEFAULT 'isolated',
  observation_seconds INT UNSIGNED NOT NULL DEFAULT 300,
  started_at DATETIME(6) NULL,
  stopped_at DATETIME(6) NULL,
  expires_at DATETIME(6) NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_sandbox_instances_public_id (public_id),
  KEY idx_personal_sandbox_instances_workspace_status (workspace_id, status, created_at),
  KEY idx_personal_sandbox_instances_case_status (case_id, status),
  KEY idx_personal_sandbox_instances_job (analysis_job_id),
  KEY idx_personal_sandbox_instances_expires (expires_at),
  CONSTRAINT fk_personal_sandbox_instances_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_sandbox_instances_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_sandbox_instances_job
    FOREIGN KEY (analysis_job_id) REFERENCES personal_analysis_jobs(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_sandbox_instances_image
    FOREIGN KEY (sandbox_image_id) REFERENCES sandbox_images(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_sandbox_instances_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_sandbox_instances_status CHECK (status IN ('queued', 'starting', 'running', 'observing', 'stopping', 'stopped', 'failed', 'expired')),
  CONSTRAINT chk_personal_sandbox_instances_provider CHECK (runtime_provider IN ('docker', 'qemu', 'firecracker', 'lxc', 'libvirt')),
  CONSTRAINT chk_personal_sandbox_instances_network CHECK (network_mode IN ('isolated', 'fake_internet', 'controlled_egress', 'offline'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_telemetry_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  analysis_job_id BIGINT UNSIGNED NULL,
  sandbox_instance_id BIGINT UNSIGNED NULL,
  event_time DATETIME(6) NOT NULL,
  event_category VARCHAR(60) NOT NULL,
  event_type VARCHAR(100) NOT NULL,
  actor_process VARCHAR(512) NULL,
  target_value VARCHAR(1024) NULL,
  severity VARCHAR(20) NOT NULL DEFAULT 'info',
  event_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_telemetry_events_public_id (public_id),
  KEY idx_personal_telemetry_events_workspace_time (workspace_id, event_time),
  KEY idx_personal_telemetry_events_job_time (analysis_job_id, event_time),
  KEY idx_personal_telemetry_events_sandbox_time (sandbox_instance_id, event_time),
  KEY idx_personal_telemetry_events_case_category_time (case_id, event_category, event_time),
  KEY idx_personal_telemetry_events_category_type (event_category, event_type),
  KEY idx_personal_telemetry_events_target (target_value(191)),
  CONSTRAINT fk_personal_telemetry_events_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_telemetry_events_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_telemetry_events_job
    FOREIGN KEY (analysis_job_id) REFERENCES personal_analysis_jobs(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_telemetry_events_sandbox
    FOREIGN KEY (sandbox_instance_id) REFERENCES personal_sandbox_instances(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_telemetry_events_category CHECK (event_category IN ('process', 'network', 'dns', 'file', 'registry', 'powershell', 'memory', 'sandbox', 'detection', 'classification')),
  CONSTRAINT chk_personal_telemetry_events_severity CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_case_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  case_id BIGINT UNSIGNED NOT NULL,
  generated_by BIGINT UNSIGNED NULL,
  title VARCHAR(220) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'draft',
  format VARCHAR(30) NOT NULL DEFAULT 'markdown',
  content_markdown MEDIUMTEXT NULL,
  storage_object_id BIGINT UNSIGNED NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_case_reports_public_id (public_id),
  KEY idx_personal_case_reports_case_status (case_id, status),
  KEY idx_personal_case_reports_generated_by (generated_by),
  KEY idx_personal_case_reports_storage (storage_object_id),
  CONSTRAINT fk_personal_case_reports_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_reports_generated_by
    FOREIGN KEY (generated_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_personal_case_reports_storage
    FOREIGN KEY (storage_object_id) REFERENCES storage_objects(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_case_reports_status CHECK (status IN ('draft', 'generating', 'ready', 'failed', 'archived')),
  CONSTRAINT chk_personal_case_reports_format CHECK (format IN ('markdown', 'pdf', 'docx', 'html'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_lab_attempts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  lab_template_id BIGINT UNSIGNED NOT NULL,
  lab_version_id BIGINT UNSIGNED NOT NULL,
  started_by BIGINT UNSIGNED NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'started',
  score DECIMAL(8,2) NULL,
  started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  completed_at DATETIME(6) NULL,
  expires_at DATETIME(6) NULL,
  metadata JSON NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_lab_attempts_public_id (public_id),
  KEY idx_personal_lab_attempts_workspace_status (workspace_id, status, started_at),
  KEY idx_personal_lab_attempts_lab_version (lab_version_id),
  KEY idx_personal_lab_attempts_started_by (started_by),
  KEY idx_personal_lab_attempts_expires (expires_at),
  CONSTRAINT fk_personal_lab_attempts_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_lab_attempts_template
    FOREIGN KEY (lab_template_id) REFERENCES lab_templates(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_personal_lab_attempts_version
    FOREIGN KEY (lab_version_id) REFERENCES lab_versions(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_personal_lab_attempts_started_by
    FOREIGN KEY (started_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_lab_attempts_status CHECK (status IN ('started', 'running', 'completed', 'failed', 'expired', 'abandoned'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_realtime_rooms (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  workspace_id BIGINT UNSIGNED NOT NULL,
  case_id BIGINT UNSIGNED NULL,
  room_type VARCHAR(40) NOT NULL DEFAULT 'case_chat',
  title VARCHAR(180) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_realtime_rooms_public_id (public_id),
  KEY idx_personal_realtime_rooms_workspace_type (workspace_id, room_type),
  KEY idx_personal_realtime_rooms_case (case_id),
  CONSTRAINT fk_personal_realtime_rooms_workspace
    FOREIGN KEY (workspace_id) REFERENCES personal_workspaces(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_realtime_rooms_case
    FOREIGN KEY (case_id) REFERENCES personal_cases(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_personal_realtime_rooms_type CHECK (room_type IN ('case_chat', 'lab_sync', 'analysis_watch', 'canvas_collab'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personal_realtime_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  room_id BIGINT UNSIGNED NOT NULL,
  sender_user_id BIGINT UNSIGNED NULL,
  message_type VARCHAR(40) NOT NULL DEFAULT 'text',
  body TEXT NULL,
  payload_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_personal_realtime_messages_public_id (public_id),
  KEY idx_personal_realtime_messages_room_created (room_id, created_at),
  KEY idx_personal_realtime_messages_sender (sender_user_id),
  CONSTRAINT fk_personal_realtime_messages_room
    FOREIGN KEY (room_id) REFERENCES personal_realtime_rooms(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_personal_realtime_messages_sender
    FOREIGN KEY (sender_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_personal_realtime_messages_type CHECK (message_type IN ('text', 'system', 'event', 'file', 'analysis_update'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 13. APPLICATION SETTINGS / LEGAL DOCUMENTS
-- ============================================================================

CREATE TABLE application_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  setting_key VARCHAR(160) NOT NULL,
  value_json JSON NOT NULL,
  is_public TINYINT(1) NOT NULL DEFAULT 0,
  updated_by BIGINT UNSIGNED NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_application_settings_key (setting_key),
  KEY idx_application_settings_public (is_public),
  KEY idx_application_settings_updated_by (updated_by),
  CONSTRAINT fk_application_settings_updated_by
    FOREIGN KEY (updated_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE legal_documents (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  document_type VARCHAR(60) NOT NULL,
  title VARCHAR(220) NOT NULL,
  current_version_id BIGINT UNSIGNED NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_legal_documents_public_id (public_id),
  UNIQUE KEY uq_legal_documents_type (document_type),
  KEY idx_legal_documents_active (is_active),
  CONSTRAINT chk_legal_documents_type CHECK (document_type IN ('terms', 'privacy_policy', 'acceptable_use_policy', 'cookie_policy'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE legal_document_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  legal_document_id BIGINT UNSIGNED NOT NULL,
  version VARCHAR(60) NOT NULL,
  content_markdown MEDIUMTEXT NOT NULL,
  published_by BIGINT UNSIGNED NULL,
  published_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_legal_document_versions_public_id (public_id),
  UNIQUE KEY uq_legal_document_versions_doc_version (legal_document_id, version),
  KEY idx_legal_document_versions_published (published_at),
  KEY idx_legal_document_versions_published_by (published_by),
  CONSTRAINT fk_legal_document_versions_doc
    FOREIGN KEY (legal_document_id) REFERENCES legal_documents(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_legal_document_versions_published_by
    FOREIGN KEY (published_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE legal_documents
  ADD CONSTRAINT fk_legal_documents_current_version
  FOREIGN KEY (current_version_id) REFERENCES legal_document_versions(id)
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE user_legal_acceptances (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  legal_document_id BIGINT UNSIGNED NOT NULL,
  legal_document_version_id BIGINT UNSIGNED NOT NULL,
  accepted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  ip_address VARBINARY(16) NULL,
  user_agent VARCHAR(512) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_user_legal_acceptances_user_version (user_id, legal_document_version_id),
  KEY idx_user_legal_acceptances_doc (legal_document_id),
  KEY idx_user_legal_acceptances_accepted (accepted_at),
  CONSTRAINT fk_user_legal_acceptances_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_user_legal_acceptances_doc
    FOREIGN KEY (legal_document_id) REFERENCES legal_documents(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_user_legal_acceptances_version
    FOREIGN KEY (legal_document_version_id) REFERENCES legal_document_versions(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 14. SUPPORT / TECH DEPARTMENT
-- ============================================================================

CREATE TABLE support_tickets (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  requester_user_id BIGINT UNSIGNED NULL,
  subject_type VARCHAR(30) NOT NULL,
  subject_id BIGINT UNSIGNED NULL,
  title VARCHAR(240) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'open',
  priority VARCHAR(20) NOT NULL DEFAULT 'normal',
  category VARCHAR(40) NOT NULL DEFAULT 'technical',
  assigned_to BIGINT UNSIGNED NULL,
  metadata JSON NULL,
  metadata_index VARCHAR(191)
    GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.index'))) STORED,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  closed_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_support_tickets_public_id (public_id),
  KEY idx_support_tickets_requester (requester_user_id),
  KEY idx_support_tickets_subject (subject_type, subject_id),
  KEY idx_support_tickets_assigned_status (assigned_to, status),
  KEY idx_support_tickets_status_priority (status, priority, created_at),
  KEY idx_support_tickets_metadata_index (metadata_index),
  FULLTEXT KEY ft_support_tickets_search (title),
  CONSTRAINT fk_support_tickets_requester
    FOREIGN KEY (requester_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_support_tickets_assigned_to
    FOREIGN KEY (assigned_to) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_support_tickets_subject CHECK (subject_type IN ('personal_workspace', 'organization', 'platform')),
  CONSTRAINT chk_support_tickets_status CHECK (status IN ('open', 'pending', 'resolved', 'closed')),
  CONSTRAINT chk_support_tickets_priority CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  CONSTRAINT chk_support_tickets_category CHECK (category IN ('billing', 'technical', 'sandbox', 'abuse', 'account', 'security'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE support_ticket_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  ticket_id BIGINT UNSIGNED NOT NULL,
  sender_user_id BIGINT UNSIGNED NULL,
  body MEDIUMTEXT NOT NULL,
  is_internal_note TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_support_ticket_messages_public_id (public_id),
  KEY idx_support_ticket_messages_ticket_created (ticket_id, created_at),
  KEY idx_support_ticket_messages_sender (sender_user_id),
  CONSTRAINT fk_support_ticket_messages_ticket
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_support_ticket_messages_sender
    FOREIGN KEY (sender_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE support_ticket_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ticket_id BIGINT UNSIGNED NOT NULL,
  actor_user_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(80) NOT NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  KEY idx_support_ticket_events_ticket_created (ticket_id, created_at),
  KEY idx_support_ticket_events_actor (actor_user_id),
  CONSTRAINT fk_support_ticket_events_ticket
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_support_ticket_events_actor
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 15. PLATFORM SOC / SECURITY / AUDIT
-- ============================================================================

CREATE TABLE platform_security_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(80) NOT NULL,
  severity VARCHAR(20) NOT NULL DEFAULT 'low',
  ip_address VARBINARY(16) NULL,
  user_agent VARCHAR(512) NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  resolved_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_platform_security_events_public_id (public_id),
  KEY idx_platform_security_events_user_created (user_id, created_at),
  KEY idx_platform_security_events_type_severity_created (event_type, severity, created_at),
  KEY idx_platform_security_events_resolved (resolved_at),
  CONSTRAINT fk_platform_security_events_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_platform_security_events_severity CHECK (severity IN ('low', 'medium', 'high', 'critical'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE platform_incidents (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  title VARCHAR(240) NOT NULL,
  severity VARCHAR(20) NOT NULL DEFAULT 'medium',
  status VARCHAR(30) NOT NULL DEFAULT 'open',
  assigned_to BIGINT UNSIGNED NULL,
  summary TEXT NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  resolved_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_platform_incidents_public_id (public_id),
  KEY idx_platform_incidents_status_severity (status, severity, created_at),
  KEY idx_platform_incidents_assigned (assigned_to, status),
  CONSTRAINT fk_platform_incidents_assigned
    FOREIGN KEY (assigned_to) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_platform_incidents_severity CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  CONSTRAINT chk_platform_incidents_status CHECK (status IN ('open', 'investigating', 'resolved', 'closed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE audit_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  actor_user_id BIGINT UNSIGNED NULL,
  subject_type VARCHAR(80) NOT NULL,
  subject_id BIGINT UNSIGNED NULL,
  action VARCHAR(120) NOT NULL,
  ip_address VARBINARY(16) NULL,
  user_agent VARCHAR(512) NULL,
  metadata JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_audit_logs_public_id (public_id),
  KEY idx_audit_logs_actor_created (actor_user_id, created_at),
  KEY idx_audit_logs_subject_created (subject_type, subject_id, created_at),
  KEY idx_audit_logs_action_created (action, created_at),
  KEY idx_audit_logs_created (created_at),
  CONSTRAINT fk_audit_logs_actor
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 16. SYSTEM RELIABILITY: IDEMPOTENCY, OUTBOX, NOTIFICATIONS
-- ============================================================================

CREATE TABLE idempotency_keys (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  key_hash BINARY(32) NOT NULL,
  user_id BIGINT UNSIGNED NULL,
  request_path VARCHAR(255) NOT NULL,
  request_hash BINARY(32) NOT NULL,
  response_status INT NULL,
  response_body_json JSON NULL,
  expires_at DATETIME(6) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (id),
  UNIQUE KEY uq_idempotency_keys_hash (key_hash),
  KEY idx_idempotency_keys_user_path (user_id, request_path),
  KEY idx_idempotency_keys_expires (expires_at),
  CONSTRAINT fk_idempotency_keys_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE outbox_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  aggregate_type VARCHAR(100) NOT NULL,
  aggregate_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(120) NOT NULL,
  payload_json JSON NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  next_attempt_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  processed_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_outbox_events_public_id (public_id),
  KEY idx_outbox_events_status_next (status, next_attempt_at, created_at),
  KEY idx_outbox_events_aggregate (aggregate_type, aggregate_id),
  KEY idx_outbox_events_type_created (event_type, created_at),
  CONSTRAINT chk_outbox_events_status CHECK (status IN ('pending', 'processing', 'sent', 'failed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE notifications (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id CHAR(26) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  notification_type VARCHAR(80) NOT NULL,
  title VARCHAR(220) NOT NULL,
  body TEXT NULL,
  link_url VARCHAR(1024) NULL,
  metadata JSON NULL,
  read_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  deleted_at DATETIME(6) NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_notifications_public_id (public_id),
  KEY idx_notifications_user_read_created (user_id, read_at, created_at),
  KEY idx_notifications_user_type_created (user_id, notification_type, created_at),
  CONSTRAINT fk_notifications_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 17. OPTIONAL SEED DATA: PLATFORM PERMISSIONS, ROLES, BASE PLANS
-- ============================================================================

INSERT INTO platform_permissions (public_id, permission_key, resource, action, description, is_system) VALUES
(CONCAT('PERM', LPAD(1, 22, '0')), 'platform.users.read', 'platform.users', 'read', 'Read platform users', 1),
(CONCAT('PERM', LPAD(2, 22, '0')), 'platform.users.manage', 'platform.users', 'manage', 'Manage platform users', 1),
(CONCAT('PERM', LPAD(3, 22, '0')), 'platform.organizations.read', 'platform.organizations', 'read', 'Read organizations', 1),
(CONCAT('PERM', LPAD(4, 22, '0')), 'platform.organizations.manage', 'platform.organizations', 'manage', 'Manage organizations', 1),
(CONCAT('PERM', LPAD(5, 22, '0')), 'platform.finance.read', 'platform.finance', 'read', 'Read finance and invoices', 1),
(CONCAT('PERM', LPAD(6, 22, '0')), 'platform.finance.manage', 'platform.finance', 'manage', 'Manage finance settings', 1),
(CONCAT('PERM', LPAD(7, 22, '0')), 'platform.plans.manage', 'platform.plans', 'manage', 'Manage plans and limits', 1),
(CONCAT('PERM', LPAD(8, 22, '0')), 'platform.settings.manage', 'platform.settings', 'manage', 'Manage application settings', 1),
(CONCAT('PERM', LPAD(9, 22, '0')), 'platform.policies.manage', 'platform.policies', 'manage', 'Manage legal documents and policies', 1),
(CONCAT('PERM', LPAD(10, 22, '0')), 'platform.support.read', 'platform.support', 'read', 'Read support tickets', 1),
(CONCAT('PERM', LPAD(11, 22, '0')), 'platform.support.respond', 'platform.support', 'respond', 'Respond to support tickets', 1),
(CONCAT('PERM', LPAD(12, 22, '0')), 'platform.soc.read', 'platform.soc', 'read', 'Read platform SOC events', 1),
(CONCAT('PERM', LPAD(13, 22, '0')), 'platform.soc.manage', 'platform.soc', 'manage', 'Manage platform SOC incidents', 1),
(CONCAT('PERM', LPAD(14, 22, '0')), 'platform.audit.read', 'platform.audit', 'read', 'Read audit logs', 1),
(CONCAT('PERM', LPAD(15, 22, '0')), 'platform.workers.manage', 'platform.workers', 'manage', 'Manage runtime workers', 1)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  is_system = VALUES(is_system);

INSERT INTO platform_roles (public_id, name, slug, description, color, position, is_default, is_system_role, is_mutable) VALUES
(CONCAT('ROLE', LPAD(1, 22, '0')), 'Platform Owner', 'platform-owner', 'Full application owner access', '#ef4444', 1000, 0, 1, 0),
(CONCAT('ROLE', LPAD(2, 22, '0')), 'Platform Admin', 'platform-admin', 'Application administration access', '#f97316', 900, 0, 1, 1),
(CONCAT('ROLE', LPAD(3, 22, '0')), 'Finance Manager', 'finance-manager', 'Billing and finance operations', '#22c55e', 700, 0, 1, 1),
(CONCAT('ROLE', LPAD(4, 22, '0')), 'Technical Support', 'technical-support', 'Support ticket response access', '#3b82f6', 500, 0, 1, 1),
(CONCAT('ROLE', LPAD(5, 22, '0')), 'Platform SOC', 'platform-soc', 'Platform security monitoring access', '#8b5cf6', 600, 0, 1, 1)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  color = VALUES(color),
  position = VALUES(position);

INSERT INTO platform_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, 1
FROM platform_roles r
JOIN platform_permissions p
WHERE r.slug = 'platform-owner'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

INSERT INTO platform_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, 1
FROM platform_roles r
JOIN platform_permissions p ON p.permission_key IN (
  'platform.users.read',
  'platform.organizations.read',
  'platform.organizations.manage',
  'platform.support.read',
  'platform.support.respond',
  'platform.audit.read',
  'platform.workers.manage'
)
WHERE r.slug = 'platform-admin'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

INSERT INTO platform_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, 1
FROM platform_roles r
JOIN platform_permissions p ON p.permission_key IN (
  'platform.finance.read',
  'platform.finance.manage',
  'platform.plans.manage'
)
WHERE r.slug = 'finance-manager'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

INSERT INTO platform_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, 1
FROM platform_roles r
JOIN platform_permissions p ON p.permission_key IN (
  'platform.support.read',
  'platform.support.respond'
)
WHERE r.slug = 'technical-support'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

INSERT INTO platform_role_permissions (role_id, permission_id, allowed)
SELECT r.id, p.id, 1
FROM platform_roles r
JOIN platform_permissions p ON p.permission_key IN (
  'platform.soc.read',
  'platform.soc.manage',
  'platform.audit.read'
)
WHERE r.slug = 'platform-soc'
ON DUPLICATE KEY UPDATE allowed = VALUES(allowed);

INSERT INTO plans (public_id, name, slug, audience, description, is_active, is_public, sort_order) VALUES
(CONCAT('PLAN', LPAD(1, 22, '0')), 'Trial', 'trial', 'trial', 'Limited trial access with weekly reset limits.', 1, 1, 10),
(CONCAT('PLAN', LPAD(2, 22, '0')), 'Personal Pro', 'personal-pro', 'personal', 'Personal cyber workspace with larger sandbox and analysis limits.', 1, 1, 20),
(CONCAT('PLAN', LPAD(3, 22, '0')), 'Organization Team', 'organization-team', 'organization', 'Organization workspace with team collaboration and stronger limits.', 1, 1, 30),
(CONCAT('PLAN', LPAD(4, 22, '0')), 'Organization Enterprise', 'organization-enterprise', 'organization', 'High-capacity organization plan for teams and SOC labs.', 1, 1, 40)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  is_active = VALUES(is_active),
  is_public = VALUES(is_public),
  sort_order = VALUES(sort_order);

-- Add or edit plan limits as your pricing model evolves.
-- limit_value = -1 means unlimited at application level.
INSERT INTO plan_limits (public_id, plan_id, limit_key, limit_value, reset_interval)
SELECT CONCAT('LIMT', LPAD(seed.seq, 22, '0')), p.id, seed.limit_key, seed.limit_value, seed.reset_interval
FROM plans p
JOIN (
  SELECT 1 seq, 'trial' slug, 'sandbox_minutes_weekly' limit_key, 300 limit_value, 'weekly' reset_interval UNION ALL
  SELECT 2, 'trial', 'analysis_jobs_weekly', 20, 'weekly' UNION ALL
  SELECT 3, 'trial', 'dynamic_malware_runs_weekly', 5, 'weekly' UNION ALL
  SELECT 4, 'trial', 'max_storage_gb', 2, 'none' UNION ALL
  SELECT 5, 'trial', 'max_parallel_sandboxes', 1, 'none' UNION ALL
  SELECT 6, 'personal-pro', 'sandbox_minutes_weekly', 1800, 'weekly' UNION ALL
  SELECT 7, 'personal-pro', 'analysis_jobs_weekly', 200, 'weekly' UNION ALL
  SELECT 8, 'personal-pro', 'dynamic_malware_runs_weekly', 50, 'weekly' UNION ALL
  SELECT 9, 'personal-pro', 'max_storage_gb', 50, 'none' UNION ALL
  SELECT 10, 'personal-pro', 'max_personal_collaborators', 5, 'none' UNION ALL
  SELECT 11, 'organization-team', 'sandbox_minutes_weekly', 10000, 'weekly' UNION ALL
  SELECT 12, 'organization-team', 'analysis_jobs_weekly', 2000, 'weekly' UNION ALL
  SELECT 13, 'organization-team', 'dynamic_malware_runs_weekly', 300, 'weekly' UNION ALL
  SELECT 14, 'organization-team', 'max_storage_gb', 500, 'none' UNION ALL
  SELECT 15, 'organization-team', 'max_organization_members', 25, 'none' UNION ALL
  SELECT 16, 'organization-enterprise', 'sandbox_minutes_weekly', -1, 'weekly' UNION ALL
  SELECT 17, 'organization-enterprise', 'analysis_jobs_weekly', -1, 'weekly' UNION ALL
  SELECT 18, 'organization-enterprise', 'dynamic_malware_runs_weekly', -1, 'weekly' UNION ALL
  SELECT 19, 'organization-enterprise', 'max_storage_gb', -1, 'none' UNION ALL
  SELECT 20, 'organization-enterprise', 'max_organization_members', -1, 'none'
) seed ON seed.slug = p.slug
ON DUPLICATE KEY UPDATE
  limit_value = VALUES(limit_value),
  reset_interval = VALUES(reset_interval);

-- ============================================================================
-- END OF MASTER SCHEMA
-- ============================================================================
