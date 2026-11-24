(function () {
    const menuButton = document.querySelector('[data-doctor-menu]');
    const scrim = document.querySelector('[data-doctor-scrim]');

    function setMenu(open) {
        document.body.classList.toggle('doctor-menu-open', open);
        if (menuButton) menuButton.setAttribute('aria-expanded', String(open));
    }

    if (menuButton) menuButton.addEventListener('click', function () {
        setMenu(!document.body.classList.contains('doctor-menu-open'));
    });
    if (scrim) scrim.addEventListener('click', function () { setMenu(false); });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') setMenu(false);
    });
    document.querySelectorAll('.doctor-sidebar a').forEach(function (link) {
        link.addEventListener('click', function () { setMenu(false); });
    });

    document.querySelectorAll('form[enctype="multipart/form-data"]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (event.defaultPrevented) return;
            const submitter = event.submitter;
            if (!submitter || submitter.name !== 'action' || submitter.value !== 'complete') return;
            submitter.disabled = true;
            submitter.setAttribute('aria-busy', 'true');
            submitter.textContent = 'Đang hoàn tất…';
        });
    });
})();
