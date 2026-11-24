(function () {
    document.querySelectorAll('[data-invoice-tabs]').forEach((tabBar) => {
        const tabs = Array.from(tabBar.querySelectorAll('[role="tab"]'));
        const panels = tabs.map((tab) => document.getElementById(tab.getAttribute('aria-controls')));
        if (!tabs.length || panels.some((panel) => !panel)) return;

        const defaultKey = tabBar.dataset.defaultTab || 'hoa-don';
        const initialTab = tabs.find((tab) => tab.id === `tab-${defaultKey}`) || tabs[0];
        const activate = (activeTab, focus = false) => {
            tabs.forEach((tab, index) => {
                const active = tab === activeTab;
                tab.setAttribute('aria-selected', String(active));
                tab.tabIndex = active ? 0 : -1;
                panels[index].hidden = !active;
            });
            if (focus) activeTab.focus();
        };

        tabBar.classList.add('invoice-tabs-enhanced');
        tabs.forEach((tab, index) => {
            tab.addEventListener('click', (event) => {
                event.preventDefault();
                activate(tab);
            });
            tab.addEventListener('keydown', (event) => {
                let next = null;
                if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
                if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
                if (event.key === 'Home') next = tabs[0];
                if (event.key === 'End') next = tabs[tabs.length - 1];
                if (next) {
                    event.preventDefault();
                    activate(next, true);
                }
            });
        });
        activate(initialTab);
    });

    const toggle = document.querySelector('[data-desk-menu]');
    const scrim = document.querySelector('[data-desk-scrim]');
    const closeMenu = () => {
        document.body.classList.remove('desk-menu-open');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
    };
    if (toggle) toggle.addEventListener('click', () => {
        const open = document.body.classList.toggle('desk-menu-open');
        toggle.setAttribute('aria-expanded', String(open));
    });
    if (scrim) scrim.addEventListener('click', closeMenu);
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMenu(); });
    document.querySelectorAll('.desk-sidebar a').forEach((link) => link.addEventListener('click', closeMenu));

    document.querySelectorAll('.desk-main form[method="post"]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            if (event.defaultPrevented || form.dataset.submitting === 'true') {
                event.preventDefault();
                return;
            }
            form.dataset.submitting = 'true';
            const button = event.submitter;
            if (button && button.tagName === 'BUTTON') {
                button.disabled = true;
                button.setAttribute('aria-busy', 'true');
            }
        });
    });
})();
