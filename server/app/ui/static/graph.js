/*
 * Force-directed graph used by the architecture and dependency pages.
 * renderGraph(container, {nodes: [{id, label, type}], links: [{source, target, kind, details}]})
 */
(function () {
    const COLORS = {
        module: "#2563eb",
        component: "#64748b",
        endpoint: "#2563eb",
        datastore: "#059669",
        table: "#059669",
        cache: "#d97706",
        broker: "#7c3aed",
        topic: "#7c3aed",
        external_service: "#dc2626",
        service_call: "#0891b2",
        gateway_route: "#0891b2",
        infrastructure: "#475569",
        storage: "#0d9488",
        email: "#db2777",
        search: "#ca8a04",
        related_endpoint: "#94a3b8",
    };

    const STEREOTYPE_COLORS = {
        rest_controller: "#2563eb", controller: "#2563eb", service: "#0891b2",
        repository: "#059669", feign_client: "#dc2626", message_listener: "#7c3aed",
        configuration: "#94a3b8", component: "#64748b",
    };

    const RADIUS = { module: 22, endpoint: 24, component: 12, related_endpoint: 9 };

    function color(d) {
        if (d.type === "component") return STEREOTYPE_COLORS[d.stereotype] || COLORS.component;
        return COLORS[d.type] || "#94a3b8";
    }

    function radius(d) {
        return RADIUS[d.type] || 16;
    }

    window.graphColors = Object.assign({}, COLORS);

    window.renderGraph = function (container, data, options) {
        options = options || {};
        container.innerHTML = "";

        if (!data || !data.nodes || data.nodes.length === 0) {
            const empty = document.createElement("div");
            empty.className = "p-8 text-center text-slate-500 text-sm";
            empty.textContent = "Nothing to show for this view.";
            container.appendChild(empty);
            return;
        }

        const width = container.clientWidth || 900;
        const height = options.height || 560;

        // d3 mutates node/link objects: work on copies
        const keep = options.nodeFilter || (() => true);
        const nodes = data.nodes.filter(keep).map(n => Object.assign({}, n));
        const ids = new Set(nodes.map(n => n.id));
        // one line per node pair: parallel edges (injects + calls) are merged
        const merged = new Map();
        data.links.filter(l => ids.has(l.source) && ids.has(l.target)).forEach(l => {
            const key = l.source + "\u0000" + l.target;
            const existing = merged.get(key);
            if (!existing) {
                merged.set(key, Object.assign({}, l, {details: (l.details || []).slice()}));
            } else {
                if (!existing.kind.split(", ").includes(l.kind)) existing.kind += ", " + l.kind;
                existing.details = existing.details.concat(l.details || []);
            }
        });
        const links = Array.from(merged.values());
        const showLinkLabels = links.length <= 30;

        const svg = d3.select(container).append("svg")
            .attr("width", width).attr("height", height)
            .attr("viewBox", [0, 0, width, height]);

        svg.append("defs").append("marker")
            .attr("id", "arrow-" + container.id)
            .attr("viewBox", "0 -5 10 10").attr("refX", 10).attr("refY", 0)
            .attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto")
            .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#94a3b8");

        const root = svg.append("g");
        const zoom = d3.zoom().scaleExtent([0.1, 4]).on("zoom", e => root.attr("transform", e.transform));
        svg.call(zoom);

        const tooltip = document.createElement("div");
        tooltip.className = "graph-tooltip";
        container.appendChild(tooltip);

        function showTooltip(event, lines) {
            tooltip.textContent = "";
            lines.filter(Boolean).forEach((line, i) => {
                const div = document.createElement("div");
                div.textContent = line;
                if (i === 0) div.style.fontWeight = "600";
                tooltip.appendChild(div);
            });
            const box = container.getBoundingClientRect();
            tooltip.style.left = (event.clientX - box.left + 12) + "px";
            tooltip.style.top = (event.clientY - box.top + 12) + "px";
            tooltip.style.display = "block";
        }

        function hideTooltip() {
            tooltip.style.display = "none";
        }

        const simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(d => d.id).distance(l => l.kind === "contains" ? 40 : 110))
            .force("charge", d3.forceManyBody().strength(-420))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("x", d3.forceX(width / 2).strength(0.07))
            .force("y", d3.forceY(height / 2).strength(0.07))
            .force("collide", d3.forceCollide().radius(d => radius(d) + 22));

        const link = root.append("g").selectAll("line").data(links).join("line")
            .attr("stroke", "#cbd5e1").attr("stroke-width", 1.6)
            .attr("marker-end", "url(#arrow-" + container.id + ")")
            .on("mousemove", (event, d) => showTooltip(event, [
                (d.source.label || d.source.id) + " → " + (d.target.label || d.target.id),
                "kind: " + d.kind,
            ].concat((d.details || []).slice(0, 8))))
            .on("mouseleave", hideTooltip);

        const linkLabel = root.append("g").selectAll("text").data(showLinkLabels ? links : []).join("text")
            .attr("class", "graph-link-label").text(d => d.kind);

        const node = root.append("g").selectAll("g").data(nodes).join("g")
            .call(d3.drag()
                .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
                .on("end", (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

        node.append("circle")
            .attr("r", radius).attr("fill", color).attr("fill-opacity", 0.9)
            .attr("stroke", "#fff").attr("stroke-width", 2);

        node.append("text")
            .attr("class", "graph-node-label")
            .attr("dy", d => radius(d) + 13)
            .text(d => (d.label || d.id).length > 32 ? (d.label || d.id).slice(0, 30) + "…" : (d.label || d.id));

        node.on("mousemove", (event, d) => showTooltip(event, [
            d.label || d.id,
            "type: " + (d.stereotype || d.type),
            d.module !== undefined && d.module !== null ? "module: " + (d.module || "(root)") : null,
            d.host ? "host: " + d.host : null,
            d.database ? "database: " + d.database : null,
            d.url ? "url: " + d.url : null,
            d.port ? "port: " + d.port : null,
        ])).on("mouseleave", hideTooltip);

        if (options.onNodeClick) {
            node.style("cursor", "pointer").on("click", (event, d) => options.onNodeClick(d));
        }

        function shorten(d) {
            const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const r = radius(d.target) + 4;
            return { x: d.target.x - dx / dist * r, y: d.target.y - dy / dist * r };
        }

        // fit the whole graph in view once the layout settles
        function fit() {
            if (!nodes.length) return;
            const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
            const minX = Math.min(...xs) - 60, maxX = Math.max(...xs) + 60;
            const minY = Math.min(...ys) - 40, maxY = Math.max(...ys) + 50;
            const scale = Math.min(1.5, 0.95 / Math.max((maxX - minX) / width, (maxY - minY) / height));
            const tx = width / 2 - scale * (minX + maxX) / 2, ty = height / 2 - scale * (minY + maxY) / 2;
            svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
        }
        simulation.on("end", fit);
        setTimeout(fit, 1500);

        simulation.on("tick", () => {
            link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
                .attr("x2", d => shorten(d).x).attr("y2", d => shorten(d).y);
            linkLabel.attr("x", d => (d.source.x + d.target.x) / 2).attr("y", d => (d.source.y + d.target.y) / 2);
            node.attr("transform", d => `translate(${d.x},${d.y})`);
        });
    };
})();
