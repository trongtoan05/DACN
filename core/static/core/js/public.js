(function () {
    const menuButton = document.querySelector('.ev-menu-toggle');
    const navigation = document.getElementById('ev-navigation');
    if (menuButton && navigation) {
        menuButton.addEventListener('click', function () {
            const open = menuButton.getAttribute('aria-expanded') !== 'true';
            menuButton.setAttribute('aria-expanded', String(open));
            menuButton.setAttribute('aria-label', open ? 'Đóng menu' : 'Mở menu');
            navigation.classList.toggle('is-open', open);
        });
    }

    const slider = document.querySelector('[data-ev-slider]');
    if (slider) {
        const slides = Array.from(slider.querySelectorAll('.ev-slide'));
        const dots = Array.from(slider.querySelectorAll('.ev-slider-dot'));
        let current = 0;
        let timer = null;
        let paused = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let userInteracted = false;
        const show = (index) => {
            current = (index + slides.length) % slides.length;
            slides.forEach((slide, i) => {
                const active = i === current;
                slide.classList.toggle('is-active', active);
                slide.setAttribute('aria-hidden', String(!active));
            });
            dots.forEach((dot, i) => dot.setAttribute('aria-current', String(i === current)));
        };
        const stop = () => { if (timer) window.clearInterval(timer); timer = null; };
        const start = () => {
            stop();
            if (!paused && !userInteracted && slides.length > 1) timer = window.setInterval(() => show(current + 1), 6500);
        };
        const pauseAfterInteraction = () => { userInteracted = true; stop(); };
        slider.querySelector('[data-slide-prev]')?.addEventListener('click', () => { show(current - 1); pauseAfterInteraction(); });
        slider.querySelector('[data-slide-next]')?.addEventListener('click', () => { show(current + 1); pauseAfterInteraction(); });
        dots.forEach((dot, i) => dot.addEventListener('click', () => { show(i); pauseAfterInteraction(); }));
        slider.addEventListener('pointerenter', stop);
        slider.addEventListener('pointerleave', start);
        slider.addEventListener('focusin', pauseAfterInteraction);
        slider.addEventListener('focusout', start);
        slider.addEventListener('pointerdown', pauseAfterInteraction);
        slider.addEventListener('pointerup', start);
        slider.querySelectorAll('a,button').forEach((control) => control.addEventListener('click', pauseAfterInteraction));
        show(0);
        start();
    }

    const quickForm = document.getElementById('quick-booking-form');
    if (quickForm) {
        const branch = quickForm.querySelector('[name="chi_nhanh"]');
        const picker = quickForm.querySelector('#quick-service-picker');
        const summary = picker?.querySelector('[data-selected-services-label]');
        const error = quickForm.querySelector('[data-service-error]');
        const options = Array.from(picker?.querySelectorAll('[data-service-option]') || []);
        const updateSelectedLabel = () => {
            const selected = options.map((option) => option.querySelector('input'))
                .filter((input) => input?.checked)
                .map((input) => input.closest('label').querySelector('span').childNodes[0].textContent.trim());
            if (!summary) return;
            summary.textContent = selected.length === 0 ? 'Chọn dịch vụ'
                : selected.length <= 2 ? selected.join(', ') : `${selected.length} dịch vụ đã chọn`;
        };
        const syncServices = () => {
            const chosen = branch?.value || '';
            options.forEach((option) => {
                const input = option.querySelector('input');
                const branches = (option.dataset.branches || '').split(',').filter(Boolean);
                const available = Boolean(chosen && branches.includes(chosen));
                option.hidden = !available;
                input.disabled = !available;
                if (!available) input.checked = false;
            });
            updateSelectedLabel();
            if (error) error.hidden = true;
        };
        branch?.addEventListener('change', syncServices);
        options.forEach((option) => option.querySelector('input')?.addEventListener('change', updateSelectedLabel));
        quickForm.addEventListener('submit', (event) => {
            const hasSelection = options.some((option) => {
                const input = option.querySelector('input');
                return input && !input.disabled && input.checked;
            });
            if (!hasSelection) {
                event.preventDefault();
                if (error) error.hidden = false;
                picker?.setAttribute('open', '');
                picker?.querySelector('summary')?.focus();
            }
        });
        syncServices();
    }
})();
