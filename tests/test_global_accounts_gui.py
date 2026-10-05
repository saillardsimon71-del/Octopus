import os
import sys

import pytest

ctk = pytest.importorskip('customtkinter')
if sys.platform != 'win32' and not os.environ.get('DISPLAY'):
    pytest.skip('display required', allow_module_level=True)

from agents.gui.workbench_v2 import WorkbenchV2
from octopus import mandates, resources


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_simple_form_adds_global_account_without_opening_real_connection(monkeypatch):
    monkeypatch.setattr(WorkbenchV2, '_load_snapshot', lambda self: None)
    opened = []
    monkeypatch.setattr(WorkbenchV2, '_open_account', lambda self, key: opened.append(key))
    app = WorkbenchV2()
    try:
        app._account_form()
        app.update()
        window = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        widgets = list(descendants(window))
        labels = [w.cget('text') for w in widgets if isinstance(w, ctk.CTkLabel)]
        assert 'Service' in labels and 'Nom (facultatif)' in labels
        assert not any('activité' in label.lower() or 'business_id' in label for label in labels)
        assert sum(w.winfo_viewable() for w in widgets if isinstance(w, ctk.CTkEntry)) == 1
        menu = next(w for w in widgets if isinstance(w, ctk.CTkOptionMenu))
        menu.set('Netlify')
        button = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Ajouter et connecter')
        button.invoke()
        app.update()
        assert len(opened) == 1
        resource = resources.get(opened[0])
        assert resource['label'] == 'Netlify' and resource['business'] is None
        assert resource['web_account']['provider'] == 'Netlify'
        assert resource['web_account']['domains'] == ['app.netlify.com']
    finally:
        app.destroy()


@pytest.mark.parametrize('enabled', [True, False])
def test_advanced_settings_are_collapsed_and_edit_preserves_session(monkeypatch, enabled):
    monkeypatch.setattr(WorkbenchV2, '_load_snapshot', lambda self: None)
    monkeypatch.setattr(WorkbenchV2, '_open_account', lambda self, key: None)
    resource = resources.add_account('Netlify', label='Netlify SiteQuiVend', actor='human')
    resources.set_account_session(resource['key'], 'connected')
    if not enabled:
        resources.disable_account(resource['key'], actor='human')
    mandates.grant('first', 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=[resource['key']])
    app = WorkbenchV2()
    try:
        app._account_form(resource['key'])
        app.update()
        window = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        widgets = list(descendants(window))
        advanced = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Paramètres avancés')
        advanced.invoke()
        app.update()
        assert sum(w.winfo_viewable() for w in widgets if isinstance(w, ctk.CTkEntry)) > 1
        save = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Enregistrer')
        save.invoke()
        app.update()
        assert resources.get(resource['key'])['web_account']['session_status'] == 'connected'
        assert resources.get(resource['key'])['web_account']['enabled'] is enabled
        assert bool(mandates.account_authority('first', resource['key'], 'read')) is enabled
    finally:
        app.destroy()
