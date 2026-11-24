(() => {
    const mapElement = document.getElementById("map");
    const branchElement = document.getElementById("branch-map");
    const mapFallback = document.getElementById("map-fallback");
    const mapFallbackMessage = document.getElementById("map-fallback-message");
    const statusElement = document.getElementById("route-status");
    const startSummary = document.getElementById("route-start-summary");
    const routeResult = document.getElementById("route-result");
    const routeDistance = document.getElementById("route-distance");
    const routeDuration = document.getElementById("route-duration");
    const locateButton = document.getElementById("route-locate");
    const selectButton = document.getElementById("route-select");
    const findButton = document.getElementById("route-find");
    const clearButton = document.getElementById("route-clear");
    const routeCsrfToken = document.querySelector('#route-csrf-token input[name="csrfmiddlewaretoken"]')?.value || "";
    const routingConfigured = branchElement.dataset.routingConfigured === "true";
    const coordinates = JSON.parse(document.getElementById("clinic-coordinates").textContent);
    const destinationLat = Number(coordinates.lat);
    const destinationLon = Number(coordinates.lon);
    const branchId = Number(branchElement.dataset.branchId);
    const branchName = branchElement.dataset.branchName;
    const branchAddress = branchElement.dataset.branchAddress;
    const routeApiUrl = branchElement.dataset.routeApiUrl;

    let map = null;
    let clinicMarker = null;
    let startMarker = null;
    let startPoint = null;
    let routeLayer = null;
    let selectingStart = false;
    let routeRequestVersion = 0;
    let routePending = false;

    function setStatus(message) {
        if (statusElement) statusElement.textContent = message;
    }

    function validPoint(lat, lon) {
        return Number.isFinite(lat) && Number.isFinite(lon)
            && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    }

    function formatStartPoint(point) {
        return `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
    }

    function setPickMode(enabled, updateStatus = true) {
        selectingStart = Boolean(enabled);
        selectButton?.setAttribute("aria-pressed", selectingStart ? "true" : "false");
        branchElement.classList.toggle("is-picking-start", selectingStart);
        if (updateStatus) {
            setStatus(selectingStart
                ? "B\u1ea5m v\u00e0o b\u1ea3n \u0111\u1ed3 \u0111\u1ec3 ch\u1ecdn \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u."
                : (startPoint ? "\u0110i\u1ec3m b\u1eaft \u0111\u1ea7u \u0111\u00e3 s\u1eb5n s\u00e0ng." : "Ch\u1ecdn \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u \u0111\u1ec3 t\u00ecm tuy\u1ebfn \u0111\u01b0\u1eddng tr\u00ean b\u1ea3n \u0111\u1ed3."));
        }
    }

    function removeRouteLayer() {
        if (routeLayer) {
            map.removeLayer(routeLayer);
            routeLayer = null;
        }
        routeResult.hidden = true;
        clearButton.disabled = true;
    }

    function clearRoute() {
        routeRequestVersion += 1;
        routePending = false;
        removeRouteLayer();
        findButton.disabled = !startPoint || !routingConfigured;
        setStatus(startPoint
            ? "\u0110\u00e3 x\u00f3a tuy\u1ebfn. \u0110i\u1ec3m b\u1eaft \u0111\u1ea7u v\u1eabn \u0111\u01b0\u1ee3c gi\u1eef tr\u00ean b\u1ea3n \u0111\u1ed3."
            : "Ch\u1ecdn \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u \u0111\u1ec3 t\u00ecm tuy\u1ebfn \u0111\u01b0\u1eddng tr\u00ean b\u1ea3n \u0111\u1ed3.");
    }

    function applyStartPoint(point, recalculate = false, sourceLabel = "\u0110i\u1ec3m b\u1eaft \u0111\u1ea7u") {
        const lat = Number(point.lat);
        const lng = Number(point.lng);
        if (!validPoint(lat, lng)) return;
        startPoint = L.latLng(lat, lng);
        if (!startMarker) {
            startMarker = L.marker(startPoint, {
                draggable: true,
                icon: L.divIcon({
                    className: "detail-start-marker-wrap",
                    html: '<div class="detail-start-marker"><span></span></div>',
                    iconSize: [28, 34],
                    iconAnchor: [14, 30],
                    popupAnchor: [0, -28],
                }),
            }).addTo(map);
            startMarker.bindPopup("");
            startMarker.on("dragend", () => {
                applyStartPoint(startMarker.getLatLng(), Boolean(routeLayer) || routePending, "\u0110i\u1ec3m b\u1eaft \u0111\u1ea7u \u0111\u00e3 k\u00e9o th\u1ea3");
            });
        } else {
            startMarker.setLatLng(startPoint);
        }
        startSummary.textContent = `${sourceLabel}: ${formatStartPoint(startPoint)}`;
        const popupContent = document.createElement("span");
        popupContent.textContent = `V\u1ecb tr\u00ed b\u1eaft \u0111\u1ea7u: ${formatStartPoint(startPoint)}`;
        startMarker.setPopupContent(popupContent);
        findButton.disabled = !routingConfigured || routePending;
        if (recalculate && routingConfigured) {
            requestRoute();
        } else if (!routingConfigured) {
            setStatus("Ch\u01b0a c\u1ea5u h\u00ecnh d\u1ecbch v\u1ee5 \u0111\u1ecbnh tuy\u1ebfn cho m\u00f4i tr\u01b0\u1eddng n\u00e0y.");
        } else {
            setStatus("\u0110i\u1ec3m b\u1eaft \u0111\u1ea7u \u0111\u00e3 s\u1eb5n s\u00e0ng. B\u1ea5m T\u00ecm \u0111\u01b0\u1eddng \u0111\u1ec3 t\u00ednh tuy\u1ebfn.");
        }
    }

    async function useCurrentLocation() {
        if (!window.isSecureContext) {
            setStatus("Tr\u00ecnh duy\u1ec7t ch\u1ec9 cho ph\u00e9p \u0111\u1ecbnh v\u1ecb tr\u00ean HTTPS ho\u1eb7c localhost. B\u1ea1n v\u1eabn c\u00f3 th\u1ec3 ch\u1ecdn \u0111i\u1ec3m th\u1ee7 c\u00f4ng.");
            return;
        }
        if (!navigator.geolocation) {
            setStatus("Tr\u00ecnh duy\u1ec7t kh\u00f4ng h\u1ed7 tr\u1ee3 \u0111\u1ecbnh v\u1ecb. H\u00e3y ch\u1ecdn \u0111i\u1ec3m th\u1ee7 c\u00f4ng tr\u00ean b\u1ea3n \u0111\u1ed3.");
            return;
        }
        locateButton.disabled = true;
        setStatus("\u0110ang l\u1ea5y v\u1ecb tr\u00ed c\u1ee7a b\u1ea1n...");
        navigator.geolocation.getCurrentPosition((position) => {
            locateButton.disabled = false;
            const point = L.latLng(position.coords.latitude, position.coords.longitude);
            applyStartPoint(point, Boolean(routeLayer), "V\u1ecb tr\u00ed hi\u1ec7n t\u1ea1i");
            map.setView(point, 15);
        }, (error) => {
            locateButton.disabled = false;
            if (error.code === error.PERMISSION_DENIED) {
                setStatus("B\u1ea1n \u0111\u00e3 t\u1eeb ch\u1ed1i \u0111\u1ecbnh v\u1ecb. B\u1eadt Ch\u1ecdn \u0111i\u1ec3m tr\u00ean b\u1ea3n \u0111\u1ed3 ho\u1eb7c k\u00e9o marker \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u.");
            } else if (error.code === error.TIMEOUT) {
                setStatus("Kh\u00f4ng nh\u1eadn \u0111\u01b0\u1ee3c v\u1ecb tr\u00ed k\u1ecbp th\u1eddi. H\u00e3y th\u1eed l\u1ea1i ho\u1eb7c ch\u1ecdn \u0111i\u1ec3m tr\u00ean b\u1ea3n \u0111\u1ed3.");
            } else {
                setStatus("Kh\u00f4ng l\u1ea5y \u0111\u01b0\u1ee3c v\u1ecb tr\u00ed. B\u1ea1n v\u1eabn c\u00f3 th\u1ec3 ch\u1ecdn \u0111i\u1ec3m tr\u00ean b\u1ea3n \u0111\u1ed3.");
            }
        }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
    }

    async function requestRoute() {
        if (!startPoint || !routingConfigured || !routeApiUrl) {
            setStatus(!routingConfigured
                ? "Ch\u01b0a c\u1ea5u h\u00ecnh d\u1ecbch v\u1ee5 \u0111\u1ecbnh tuy\u1ebfn cho m\u00f4i tr\u01b0\u1eddng n\u00e0y."
                : "H\u00e3y ch\u1ecdn m\u1ed9t \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u tr\u01b0\u1edbc.");
            return;
        }
        const version = ++routeRequestVersion;
        const origin = { lat: startPoint.lat, lon: startPoint.lng };
        routePending = true;
        findButton.disabled = true;
        clearButton.disabled = false;
        setStatus("\u0110ang t\u00ecm tuy\u1ebfn \u0111\u01b0\u1eddng...");
        try {
            const response = await fetch(routeApiUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": routeCsrfToken,
                    "Accept": "application/json",
                },
                body: JSON.stringify({ branch_id: branchId, start_lat: origin.lat, start_lon: origin.lon }),
            });
            const data = await response.json();
            if (version !== routeRequestVersion) return;
            if (!response.ok) throw new Error(data.error || "Kh\u00f4ng t\u00ednh \u0111\u01b0\u1ee3c tuy\u1ebfn \u0111\u01b0\u1eddng.");
            const points = data.geometry?.coordinates;
            if (data.geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2) {
                throw new Error("D\u1ecbch v\u1ee5 \u0111\u1ecbnh tuy\u1ebfn tr\u1ea3 v\u1ec1 tuy\u1ebfn kh\u00f4ng h\u1ee3p l\u1ec7.");
            }
            if (version !== routeRequestVersion) return;
            const nextRoute = L.geoJSON(data.geometry, {
                style: { color: "#0879ba", weight: 6, opacity: 0.9 },
            });
            if (!nextRoute.getBounds().isValid()) throw new Error("Tuy\u1ebfn \u0111\u01b0\u1eddng kh\u00f4ng c\u00f3 t\u1ecda \u0111\u1ed9 h\u1ee3p l\u1ec7.");
            nextRoute.addTo(map);
            removeRouteLayer();
            routeLayer = nextRoute;
            map.fitBounds(routeLayer.getBounds(), { padding: [32, 32], maxZoom: 16 });
            routeDistance.textContent = `${(data.distance_meters / 1000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} km`;
            routeDuration.textContent = `${Math.max(1, Math.round(data.duration_seconds / 60))} ph\u00fat`;
            routeResult.hidden = false;
            clearButton.disabled = false;
            setStatus("\u0110\u00e3 hi\u1ec3n th\u1ecb tuy\u1ebfn \u0111\u01b0\u1eddng giao th\u00f4ng tr\u00ean b\u1ea3n \u0111\u1ed3.");
        } catch (error) {
            if (version !== routeRequestVersion) return;
            const message = error.message || "Kh\u00f4ng t\u00ecm \u0111\u01b0\u1ee3c tuy\u1ebfn \u0111\u01b0\u1eddng. Vui l\u00f2ng th\u1eed l\u1ea1i sau.";
            setStatus(routeLayer ? `${message} Tuy\u1ebfn tr\u01b0\u1edbc v\u1eabn hi\u1ec3n th\u1ecb cho t\u1edbi khi c\u00f3 tuy\u1ebfn m\u1edbi h\u1ee3p l\u1ec7.` : message);
        } finally {
            if (version === routeRequestVersion) {
                routePending = false;
                findButton.disabled = !startPoint || !routingConfigured;
            }
        }
    }

    if (!window.L) {
        mapElement.hidden = true;
        mapFallback.hidden = false;
        mapFallbackMessage.textContent = "Leaflet ch\u01b0a t\u1ea3i \u0111\u01b0\u1ee3c; b\u1ea3n \u0111\u1ed3 v\u00e0 \u0111\u1ecbnh tuy\u1ebfn hi\u1ec7n kh\u00f4ng kh\u1ea3 d\u1ee5ng.";
        [locateButton, selectButton, findButton, clearButton].forEach((button) => {
            if (button) button.disabled = true;
        });
        setStatus("Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c th\u01b0 vi\u1ec7n b\u1ea3n \u0111\u1ed3 Leaflet.");
        return;
    }

    if (!validPoint(destinationLat, destinationLon)) {
        mapElement.hidden = true;
        mapFallback.hidden = false;
        mapFallbackMessage.textContent = "Chi nh\u00e1nh ch\u01b0a c\u00f3 t\u1ecda \u0111\u1ed9 h\u1ee3p l\u1ec7 \u0111\u1ec3 hi\u1ec3n th\u1ecb b\u1ea3n \u0111\u1ed3.";
        setStatus("Chi nh\u00e1nh ch\u01b0a c\u00f3 t\u1ecda \u0111\u1ed9 h\u1ee3p l\u1ec7.");
        [locateButton, selectButton, findButton, clearButton].forEach((button) => {
            if (button) button.disabled = true;
        });
        return;
    }

    map = L.map("map", { scrollWheelZoom: false }).setView([destinationLat, destinationLon], 15);
    const mapTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        referrerPolicy: "strict-origin-when-cross-origin",
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Routing by <a href="https://project-osrm.org/">OSRM</a>',
    });
    let failedTiles = 0;
    mapTiles.on("tileerror", () => {
        failedTiles += 1;
        if (failedTiles >= 3) {
            mapFallback.hidden = false;
            mapFallbackMessage.textContent = "Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c m\u1ed9t s\u1ed1 \u00f4 b\u1ea3n \u0111\u1ed3. V\u1ecb tr\u00ed ph\u00f2ng kh\u00e1m v\u00e0 c\u00e1c thao t\u00e1c tr\u00ean b\u1ea3n \u0111\u1ed3 v\u1eabn \u0111\u01b0\u1ee3c gi\u1eef.";
        }
    });
    mapTiles.on("load", () => {
        failedTiles = 0;
        mapFallback.hidden = true;
    });
    mapTiles.addTo(map);

    const clinicPopup = document.createElement("div");
    const clinicTitle = document.createElement("strong");
    clinicTitle.textContent = branchName;
    const clinicAddress = document.createElement("div");
    clinicAddress.textContent = branchAddress;
    clinicPopup.append(clinicTitle, clinicAddress);
    clinicMarker = L.marker([destinationLat, destinationLon], { draggable: false }).addTo(map);
    clinicMarker.bindPopup(clinicPopup);
    clinicMarker.on("click", (event) => L.DomEvent.stopPropagation(event));

    map.on("click", (event) => {
        if (!selectingStart) return;
        applyStartPoint(event.latlng, Boolean(routeLayer) || routePending, "\u0110i\u1ec3m \u0111\u00e3 ch\u1ecdn tr\u00ean b\u1ea3n \u0111\u1ed3");
        setPickMode(false, false);
    });
    locateButton?.addEventListener("click", useCurrentLocation);
    selectButton?.addEventListener("click", () => setPickMode(!selectingStart));
    findButton?.addEventListener("click", requestRoute);
    clearButton?.addEventListener("click", clearRoute);

    if (!routingConfigured) {
        setStatus("Ch\u01b0a c\u1ea5u h\u00ecnh d\u1ecbch v\u1ee5 \u0111\u1ecbnh tuy\u1ebfn cho m\u00f4i tr\u01b0\u1eddng n\u00e0y. C\u00f3 th\u1ec3 ch\u1ecdn \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u, nh\u01b0ng ch\u01b0a th\u1ec3 t\u00ednh tuy\u1ebfn.");
    } else if (branchElement.dataset.routingDemo === "true") {
        setStatus("Ch\u1ecdn \u0111i\u1ec3m b\u1eaft \u0111\u1ea7u. D\u1ecbch v\u1ee5 OSRM demo c\u00f3 th\u1ec3 ch\u1eadm ho\u1eb7c t\u1ea1m ng\u01b0ng.");
    }

    const invalidateMapSize = () => map.invalidateSize({ pan: false });
    requestAnimationFrame(invalidateMapSize);
    window.addEventListener("load", invalidateMapSize, { once: true });
    window.addEventListener("resize", invalidateMapSize);
    if ("ResizeObserver" in window) {
        new ResizeObserver(invalidateMapSize).observe(branchElement);
    }
})();
