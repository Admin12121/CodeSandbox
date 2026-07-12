from __future__ import annotations

import math

from flask import request

from codesandbox.shared.permissions import has_platform_permission
from codesandbox.shared.session import build_nav, require_platform_role
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

from .service import (
    INTERFACE_MODES,
    NETWORK_MODES,
    RUNTIME_CLASSES,
    SANDBOX_TYPES,
    TEMPLATE_STATUSES,
    UI_MODE_LABELS,
    get_platform_plans,
    get_platform_templates,
    get_template_detail,
    get_template_plan_configs,
)
from .ui_workflow import UI_WORKFLOW_CONDITIONS, UI_WORKFLOW_MODES


@router.page("/platform/sandboxes")
def platform_sandboxes():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    can_manage = user.platform_role == "system_admin" or has_platform_permission(user, "platform.sandboxes.manage")
    if not can_manage:
        return {"_redirect": "/dashboard"}
    nav = build_nav("/platform/sandboxes", user)

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "all")
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 25

    templates, total = get_platform_templates(
        search=search or None,
        status=status if status != "all" else None,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, math.ceil(total / page_size))

    template_param = request.args.get("template") or None
    tab = request.args.get("tab", "identity") if (template_param and template_param != "new") else "identity"
    selected_template = None
    if template_param == "new":
        selected_template = {
            "id": None, "slug": "", "name": "", "description": "",
            "icon_path": "", "docker_image": "", "sandbox_type": "interactive",
            "runtime_class": "container", "interface_mode": "terminal_only",
            "allowed_ui_modes": "terminal_only", "allowed_ui_mode_values": ["terminal_only"],
            "default_ui_mode": "terminal_only",
            "interface_behavior": "single", "ui_workflow_json": "",
            "ui_workflow": {"mode": "workflow", "start_node_id": None, "nodes": [], "edges": []},
            "network_mode": "disabled", "allow_root": False,
            "default_command": "", "working_dir": "/workspace",
            "input_mount_path": "/input", "output_mount_path": "/output",
            "artifact_paths": '["/output"]', "input_required": False,
            "max_upload_mb": 50, "read_only_root": True, "run_as_user": "",
            "pids_limit": 256, "allow_full_internet": False,
            "max_timeout_hr": 2, "status": "active", "runtime_config": "",
            "active_instances": 0,
        }
    elif template_param:
        selected_template = get_template_detail(template_param)

    template_plan_configs = None
    if tab == "plans" and selected_template and selected_template.get("id"):
        template_plan_configs = get_template_plan_configs(selected_template["id"])

    return {
        "_meta": {"title": "Sandboxes — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Sandbox Templates",
        "page_description": "Manage available sandbox environments",
        **_workspaces_ctx(user),
        "templates": templates,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search,
        "status_filter": status,
        "selected_template": selected_template,
        "tab": tab,
        "can_manage": can_manage,
        "sandbox_types": SANDBOX_TYPES,
        "runtime_classes": RUNTIME_CLASSES,
        "interface_modes": INTERFACE_MODES,
        "ui_mode_labels": UI_MODE_LABELS,
        "network_modes": NETWORK_MODES,
        "template_statuses": TEMPLATE_STATUSES,
        "template_plan_configs": template_plan_configs,
        "error": request.args.get("error"),
    }


@router.page("/platform/sandboxes/<template_id>/workflow")
def platform_sandbox_ui_workflow(template_id: str):
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    can_manage = user.platform_role == "system_admin" or has_platform_permission(user, "platform.sandboxes.manage")
    if not can_manage:
        return {"_redirect": "/dashboard"}
    selected_template = get_template_detail(template_id)
    if selected_template is None:
        return {"_redirect": "/platform/sandboxes"}
    nav = build_nav("/platform/sandboxes", user)
    return {
        "_meta": {"title": f"{selected_template['name']} workflow — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": f"{selected_template['name']} — Workflow",
        **_workspaces_ctx(user),
        "selected_template": selected_template,
        "can_manage": can_manage,
        "ui_workflow_modes": UI_WORKFLOW_MODES,
        "ui_workflow_conditions": UI_WORKFLOW_CONDITIONS,
        "ui_mode_labels": UI_MODE_LABELS,
    }


@router.page("/platform/sandbox-plans")
def platform_sandbox_plans():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    can_manage = user.platform_role == "system_admin" or has_platform_permission(user, "platform.sandbox_plans.manage")
    if not can_manage:
        return {"_redirect": "/dashboard"}
    nav = build_nav("/platform/sandbox-plans", user)

    plans = get_platform_plans()

    plan_param = request.args.get("plan") or None
    selected_plan = None
    if plan_param == "new":
        selected_plan = {
            "id": "", "name": "", "sort_order": len(plans),
            "ind_vcpu": 1, "ind_ram_gb": 1, "ind_disk_gb": 10, "ind_cost_hr": "0.0000",
            "org_vcpu": 2, "org_ram_gb": 2, "org_disk_gb": 20, "org_cost_hr": "0.0000",
            "min_billable_minutes": 1,
            "allowed_network_modes": '["disabled","restricted"]',
            "is_active": True,
        }
    elif plan_param:
        selected_plan = next((p for p in plans if p["id"] == plan_param), None)

    return {
        "_meta": {"title": "Sandbox Plans — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Sandbox Plans",
        "page_description": "Resource tiers and pricing for sandbox instances",
        **_workspaces_ctx(user),
        "plans": plans,
        "selected_plan": selected_plan,
        "can_manage": can_manage,
        "error": request.args.get("error"),
    }
