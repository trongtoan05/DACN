(function () {
    const container = document.getElementById('home-map');
    const dataNode = document.getElementById('map-data');
    if (!container || !dataNode) return;

    const branches = JSON.parse(dataNode.textContent || '[]');
    if (!branches.length) {
        container.innerHTML = '<div class="ev-map-empty">Ch\u01b0a c\u00f3 chi nh\u00e1nh ph\u00f9 h\u1ee3p v\u1edbi b\u1ed9 l\u1ecdc.</div>';
        return;
    }
    if (!window.L) {
        container.innerHTML = '<div class="ev-map-empty">B\u1ea3n \u0111\u1ed3 ch\u01b0a t\u1ea3i \u0111\u01b0\u1ee3c. Vui l\u00f2ng ki\u1ec3m tra k\u1ebft n\u1ed1i r\u1ed3i t\u1ea3i l\u1ea1i trang.</div>';
        return;
    }

    const map = L.map(container, {scrollWheelZoom: false, keyboard: true}).setView([branches[0].lat, branches[0].lon], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        referrerPolicy: 'strict-origin-when-cross-origin',
        attribution: '&copy; OpenStreetMap',
    }).addTo(map);

    const bounds = [];
    const markers = new Map();
    const cards = new Map();
    let userLocationMarker = null;
    const setSelected = (id) => {
        cards.forEach((card) => card.classList.toggle('is-selected', card.dataset.branchId === String(id)));
    };
    const popupLink = (href, label, className) => {
        const link = document.createElement('a');
        link.href = href;
        link.textContent = label;
        link.className = className;
        return link;
    };

    branches.forEach((branch) => {
        if (!Number.isFinite(branch.lat) || !Number.isFinite(branch.lon)
            || Math.abs(branch.lat) > 90 || Math.abs(branch.lon) > 180) return;

        const marker = L.marker([branch.lat, branch.lon]).addTo(map);
        const popup = document.createElement('div');
        popup.className = 'ev-map-popup';
        const image = document.createElement('img');
        image.src = branch.image_url;
        image.alt = '\u1ea2nh minh h\u1ecda chung cho c\u00e1c chi nh\u00e1nh EyeViora';
        image.width = 1024;
        image.height = 1024;
        image.loading = 'lazy';
        image.className = 'ev-map-popup-image';
        const note = document.createElement('small');
        note.textContent = '\u1ea2nh minh h\u1ecda chung';
        const title = document.createElement('strong');
        title.textContent = `Phòng khám mắt EyeViora — ${branch.name}`;
        const address = document.createElement('span');
        address.textContent = branch.address || branch.phuong || '';
        const status = document.createElement('span');
        status.className = 'ev-map-popup-status';
        const statusParts = ['\u0110ang ho\u1ea1t \u0111\u1ed9ng'];
        if (branch.has_reliable_hours) statusParts.push(branch.open ? '\u0110ang m\u1edf c\u1eeda' : '\u0110\u00f3ng c\u1eeda');
        status.textContent = statusParts.join(' \u00b7 ');
        const actions = document.createElement('div');
        actions.className = 'ev-map-popup-actions';
        actions.append(popupLink(branch.detail_url, 'Chi ti\u1ebft', 'ev-popup-detail'));
        if (branch.has_booking_options) actions.append(popupLink(branch.booking_url, '\u0110\u1eb7t l\u1ecbch kh\u00e1m', 'ev-popup-book'));
        popup.append(image, note, title, address, status, actions);
        marker.bindPopup(popup);
        marker.on('click', () => setSelected(branch.id));
        markers.set(String(branch.id), marker);
        bounds.push([branch.lat, branch.lon]);

        const card = document.querySelector(`[data-branch-card][data-branch-id="${Number(branch.id)}"]`);
        if (card) {
            cards.set(String(branch.id), card);
            card.querySelector('[data-branch-focus]')?.addEventListener('click', () => {
                setSelected(branch.id);
                map.flyTo(marker.getLatLng(), 15, {duration: 0.55});
                marker.openPopup();
            });
        }
    });
    if (bounds.length > 1) map.fitBounds(bounds, {padding: [28, 28], maxZoom: 14});
    else if (bounds.length) map.setView(bounds[0], 14);

    const form = document.getElementById('geo-form');
    const lat = document.getElementById('lat');
    const lon = document.getElementById('lon');
    const locate = document.getElementById('locate-btn');
    const note = document.getElementById('location-note');
    const locationIcon = L.divIcon({
        className: 'ev-user-location-icon',
        html: '<span aria-hidden="true"></span>',
        iconSize: [24, 24],
        iconAnchor: [12, 12],
    });
    const updateUserLocation = (latitude, longitude, focus = false) => {
        lat.value = Number(latitude).toFixed(6);
        lon.value = Number(longitude).toFixed(6);
        const point = [Number(latitude), Number(longitude)];
        if (!userLocationMarker) {
            userLocationMarker = L.marker(point, {draggable: true, autoPan: true, icon: locationIcon})
                .addTo(map)
                .bindTooltip('Vị trí của bạn · kéo dấu này để chỉnh vị trí');
            userLocationMarker.on('dragend', () => {
                const changed = userLocationMarker.getLatLng();
                updateUserLocation(changed.lat, changed.lng);
                if (note) note.textContent = 'Đã cập nhật vị trí. Danh sách chi nhánh được sắp xếp lại theo vị trí mới.';
                form?.requestSubmit();
            });
        } else {
            userLocationMarker.setLatLng(point);
        }
        if (focus) map.setView(point, 13);
    };

    const existingLatitude = Number(lat?.value);
    const existingLongitude = Number(lon?.value);
    if (Number.isFinite(existingLatitude) && Number.isFinite(existingLongitude)
        && Math.abs(existingLatitude) <= 90 && Math.abs(existingLongitude) <= 180) {
        updateUserLocation(existingLatitude, existingLongitude, true);
        if (note) note.textContent = 'Đã có vị trí tìm kiếm. Kéo dấu trên bản đồ để điều chỉnh vị trí.';
    }

    locate?.addEventListener('click', () => {
        if (!window.isSecureContext) {
            if (note) note.textContent = 'Trình duyệt chỉ cho phép định vị trên HTTPS hoặc localhost. Mở trang bằng địa chỉ bảo mật rồi thử lại.';
            return;
        }
        if (!navigator.geolocation) {
            if (note) note.textContent = 'Trình duyệt này không hỗ trợ định vị.';
            return;
        }
        locate.disabled = true;
        locate.textContent = 'Đang lấy vị trí...';
        navigator.geolocation.getCurrentPosition((position) => {
            updateUserLocation(position.coords.latitude, position.coords.longitude, true);
            if (note) note.textContent = 'Đã lấy vị trí. Kéo dấu trên bản đồ để chỉnh vị trí; danh sách sẽ được sắp xếp theo khoảng cách.';
            form?.requestSubmit();
        }, (error) => {
            locate.disabled = false;
            locate.textContent = 'Lấy vị trí';
            const messages = {
                1: 'Bạn chưa cấp quyền vị trí. Hãy cho phép định vị trong cài đặt trình duyệt rồi thử lại.',
                2: 'Thiết bị chưa xác định được vị trí. Kiểm tra GPS hoặc kết nối mạng rồi thử lại.',
                3: 'Lấy vị trí quá thời gian chờ. Hãy thử lại hoặc kéo dấu vị trí trên bản đồ.',
            };
            if (note) note.textContent = messages[error.code] || 'Không lấy được vị trí. Hãy thử lại hoặc kéo dấu vị trí trên bản đồ.';
        }, {enableHighAccuracy: false, timeout: 20000, maximumAge: 30000});
    });
})();
