/* Live progress for the "Processing uploads" panel; reloads the page when everything is documented. */
(function () {
    var panel = document.querySelector("[data-jobs-panel]");
    if (!panel) return;
    var repo = panel.getAttribute("data-repo");
    var url = "/api/jobs?active=true&limit=50" + (repo ? "&repo=" + encodeURIComponent(repo) : "");

    function update(list) {
        list.forEach(function (job) {
            var row = panel.querySelector('[data-job-id="' + job.id + '"]');
            if (!row) return;
            var bar = row.querySelector('[role="progressbar"]');
            var total = job.progress_total || 0, done = job.progress_done || 0;
            bar.setAttribute("aria-valuemax", total);
            bar.setAttribute("aria-valuenow", done);
            bar.firstElementChild.style.width = (total ? Math.round(done * 100 / total) : 0) + "%";
            row.querySelector("[data-job-text]").textContent =
                total ? done + " of " + total + " endpoints" : "waiting for a worker";
            var status = row.querySelector(".job-status");
            status.textContent = job.status === "running" ? "Documenting" : (job.attempts ? "Retrying soon" : "Queued");
            status.className = "job-status job-" + job.status;
        });
    }

    function poll() {
        fetch(url, {headers: {"Accept": "application/json"}})
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var active = data.jobs || [];
                var ids = active.map(function (j) { return String(j.id); });
                var shown = Array.prototype.map.call(panel.querySelectorAll("[data-job-id]"), function (el) { return el.getAttribute("data-job-id"); });
                // a job finished (or a new one arrived): show the new state of the page
                if (shown.some(function (id) { return ids.indexOf(id) < 0; }) || ids.some(function (id) { return shown.indexOf(id) < 0; })) {
                    location.reload();
                    return;
                }
                update(active);
                setTimeout(poll, 2500);
            })
            .catch(function () { setTimeout(poll, 10000); });
    }
    setTimeout(poll, 2500);
})();
