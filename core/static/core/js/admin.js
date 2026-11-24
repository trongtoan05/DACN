(function () {
    const root = document.body;
    const toggle = document.querySelector('[data-admin-menu]');
    const scrim = document.querySelector('[data-admin-scrim]');
    const sidebar = document.getElementById('admin-sidebar');

    function setOpen(open) {
        root.classList.toggle('menu-open', open);
        if (toggle) {
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Đóng menu quản trị' : 'Mở menu quản trị');
        }
    }

    if (toggle && sidebar) {
        toggle.addEventListener('click', function () {
            setOpen(!root.classList.contains('menu-open'));
        });
        if (scrim) scrim.addEventListener('click', function () { setOpen(false); });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') setOpen(false);
        });
        sidebar.addEventListener('click', function (event) {
            if (event.target.closest('a') && window.matchMedia('(max-width: 820px)').matches) setOpen(false);
        });
    }
}());
