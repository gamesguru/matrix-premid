# -*- coding: utf-8 -*-
"""
Created on Sun Mar 29 00:33:45 2026

@author: @gg:nutra.tk
"""

from matrix_premid.__main__ import ProviderConfig

# pylint: disable=missing-function-docstring


def test_load_custom_providers():
    pc = ProviderConfig()
    custom_cfg = {
        "Custom": {
            "regex": "custom_app",
            "priority": 150,
            "is_video": False,
            "enabled": True,
            "template": "Hello {title}",
        },
        "YouTube Music": {"enabled": False},
    }
    pc.load(custom_cfg)

    # Custom exists
    assert "Custom" in pc.providers
    custom_prov = pc.providers["Custom"]
    assert custom_prov.priority == 150
    assert custom_prov.pattern is not None
    assert custom_prov.template == "Hello {title}"

    # YouTube Music was overridden
    assert pc.providers["YouTube Music"].enabled is False


def test_load_invalid_regex_disables_provider():
    pc = ProviderConfig()
    custom_cfg = {"Broken": {"regex": "[unclosed bracket", "enabled": True}}
    pc.load(custom_cfg)

    assert "Broken" in pc.providers
    assert pc.providers["Broken"].enabled is False
    assert pc.providers["Broken"].pattern is None


def test_match_provider_highest_priority():
    pc = ProviderConfig()
    custom_cfg = {
        "Low Priority": {"regex": "shared", "priority": 10, "enabled": True},
        "High Priority": {"regex": "shared", "priority": 100, "enabled": True},
        "Disabled High Priority": {
            "regex": "shared",
            "priority": 200,
            "enabled": False,
        },
    }
    pc.load(custom_cfg)

    match = pc.match_provider("testing shared player")
    assert match is not None
    assert match.name == "High Priority"


def test_match_provider_no_match():
    pc = ProviderConfig()
    pc.load({})
    match = pc.match_provider("something completely random")
    assert match is None


def test_match_provider_empty_string():
    pc = ProviderConfig()
    pc.load({})
    match = pc.match_provider("")
    assert match is None
