from __future__ import annotations

import json

# ── Base policy — always enforced, never overridable ──────────────────────────

_BASE = {
    "no_host_mount": True,
    "no_docker_socket": True,
    "no_privileged": True,
    "no_platform_network": True,
    "external_monitoring": True,
    "artifact_collection": True,
    "destroy_overlay_after": True,
}

# ── Per-sandbox-type additions ─────────────────────────────────────────────────

_TYPE_LAYER: dict[str, dict] = {
    "interactive": {
        "workspace_lock": False,
        "interaction_sim": False,
        "kill_triggers": [],
        "allowed_network_overrides": None,
    },
    "malware": {
        "workspace_lock": True,
        "interaction_sim": True,
        "kill_triggers": ["network_escape", "privilege_escalation"],
        "allowed_network_overrides": ["fake_internet", "disabled", "controlled_proxy"],
    },
    "reverse_engineering": {
        "workspace_lock": True,
        "interaction_sim": False,
        "kill_triggers": ["privilege_escalation"],
        "allowed_network_overrides": ["disabled", "isolated"],
    },
    "android": {
        "workspace_lock": False,
        "interaction_sim": False,
        "kill_triggers": [],
        "allowed_network_overrides": None,
    },
    "ctf": {
        "workspace_lock": False,
        "interaction_sim": False,
        "kill_triggers": [],
        "allowed_network_overrides": ["disabled", "isolated"],
    },
}


class PolicyBuilder:
    """Merge base + type + template + plan + user config → frozen runtime_policy dict."""

    def build(
        self,
        template: dict,
        plan: dict,
        workspace_type: str = "personal",
        user_config: dict | None = None,
    ) -> dict:
        policy: dict = dict(_BASE)

        # Type layer
        sandbox_type = template.get("sandbox_type", "interactive")
        type_layer = dict(_TYPE_LAYER.get(sandbox_type, _TYPE_LAYER["interactive"]))
        policy.update(type_layer)

        # Template identity
        policy["sandbox_type"] = sandbox_type
        policy["runtime_class"] = template.get("runtime_class", "container")
        policy["docker_image"] = template.get("docker_image", "")
        policy["interface_mode"] = template.get("interface_mode", "terminal")
        policy["network_mode"] = template.get("network_mode", "disabled")
        policy["allow_root"] = bool(template.get("allow_root", False))
        policy["max_timeout_sec"] = int(template.get("max_timeout_hr", 2)) * 3600

        # type_config JSON from admin (tool profiles, kill trigger lists, etc.)
        if template.get("type_config"):
            try:
                tc = json.loads(template["type_config"])
                if isinstance(tc, dict):
                    policy["type_config"] = tc
            except (ValueError, TypeError):
                pass

        # Plan layer — org tier if org workspace
        is_org = workspace_type == "org"
        policy["vcpu"] = int(plan.get("org_vcpu" if is_org else "ind_vcpu", 1))
        policy["ram_gb"] = int(plan.get("org_ram_gb" if is_org else "ind_ram_gb", 1))
        policy["disk_gb"] = int(plan.get("org_disk_gb" if is_org else "ind_disk_gb", 10))
        policy["cost_hr"] = str(plan.get("org_cost_hr" if is_org else "ind_cost_hr", "0"))

        # User config layer — only permitted overrides
        if user_config:
            if "interface_mode" in user_config:
                policy["interface_mode"] = str(user_config["interface_mode"])
            allowed_net = type_layer.get("allowed_network_overrides")
            if allowed_net and user_config.get("network_mode") in allowed_net:
                policy["network_mode"] = str(user_config["network_mode"])

        return policy
