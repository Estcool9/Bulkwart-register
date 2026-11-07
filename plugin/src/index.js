const React = require("react");
const api = require("@plugin-host");
const UI = require("@plugin-host/ui");
const { normalizeServiceUrl, normalizeServicePath, parseResponse } = require("./lib/service.js");

const h = React.createElement;
const {
  Page,
  PageHeader,
  Stack,
  Row,
  Card,
  Text,
  Button,
  Badge,
  Alert,
  SettingRow,
  Switch,
} = UI;

const CREDENTIAL_ID = "signup-api";

async function serviceRequest(baseUrl, servicePath, path, options) {
  const opts = options || {};
  const headers = { Accept: "application/json" };
  const init = {
    method: opts.method || "GET",
    headers,
    responseType: "json",
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const response = await api.proxy.fetch(CREDENTIAL_ID, baseUrl + servicePath + path, init);
  return parseResponse(response);
}

function formatDate(timestamp) {
  const date = new Date(Number(timestamp) * 1000);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString();
}

function AdminPage() {
  const [serviceUrl, setServiceUrl] = React.useState("");
  const [servicePath, setServicePath] = React.useState("");
  const [configured, setConfigured] = React.useState(false);
  const [approvalMode, setApprovalMode] = React.useState("manual");
  const [notificationsEnabled, setNotificationsEnabled] = React.useState(false);
  const [notificationEmail, setNotificationEmail] = React.useState("");
  const [requests, setRequests] = React.useState([]);
  const [busy, setBusy] = React.useState(false);
  const [busyId, setBusyId] = React.useState(null);
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [confirmation, setConfirmation] = React.useState(null);

  async function load() {
    setBusy(true);
    setError("");
    try {
      const config = await api.config.get();
      const base = normalizeServiceUrl(config && config.serviceUrl);
      const prefix = normalizeServicePath(config && config.servicePath);
      setServiceUrl(base);
      setServicePath(prefix);
      setConfigured(true);

      const results = await Promise.all([
        serviceRequest(base, prefix, "/api/v1/admin/settings"),
        serviceRequest(base, prefix, "/api/v1/admin/requests"),
      ]);
      setApprovalMode(results[0].approvalMode === "auto" ? "auto" : "manual");
      setNotificationsEnabled(Boolean(results[0].notificationsEnabled));
      setNotificationEmail(results[0].notificationEmail || "");
      setRequests(Array.isArray(results[1].requests) ? results[1].requests : []);
    } catch (err) {
      setConfigured(false);
      setRequests([]);
      setError(err && err.message ? err.message : "Could not connect to the signup service.");
    } finally {
      setBusy(false);
    }
  }

  React.useEffect(() => {
    load();
  }, []);

  async function changeMode(nextMode) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await serviceRequest(serviceUrl, servicePath, "/api/v1/admin/settings", {
        method: "PUT",
        body: { approvalMode: nextMode },
      });
      setApprovalMode(nextMode);
      setNotice(nextMode === "auto"
        ? "Automatic approval is enabled for new requests."
        : "Manual approval is enabled for new requests.");
    } catch (err) {
      setError(err && err.message ? err.message : "Could not update approval mode.");
    } finally {
      setBusy(false);
    }
  }

  async function finishAction(item, action) {
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      const path = "/api/v1/admin/requests/" + encodeURIComponent(item.id) + "/" + action;
      const result = await serviceRequest(serviceUrl, servicePath, path, { method: "POST" });
      setNotice(action === "approve"
        ? "Mailbox created: " + (result.address || item.address)
        : "Request rejected: " + item.address);
      setConfirmation(null);
      await load();
    } catch (err) {
      setError(err && err.message ? err.message : "The request could not be processed.");
    } finally {
      setBusyId(null);
    }
  }

  if (!configured) {
    return h(Page, { flush: true },
      h(Stack, { gap: 4 },
        h(PageHeader, {
          icon: "user-plus",
          title: "Stalwart Signup Manager",
          description: "Manage public mailbox requests without leaving the Bulwark admin console.",
        }),
        error
          ? h(Alert, { tone: "warning", title: "Signup service is not configured" }, error)
          : h(Alert, { tone: "info" }, "Configure the Signup service URL and Admin API token in this plugin's settings, then reload this page."),
        h(Button, { variant: "secondary", onClick: load, disabled: busy }, "Retry connection")
      )
    );
  }

  const modeLabel = approvalMode === "auto" ? "Automatic" : "Manual";

  return h(Page, { flush: true },
    h(Stack, { gap: 4 },
      h(PageHeader, {
        icon: "user-plus",
        title: "Stalwart Signup Manager",
        description: "Review mailbox requests and choose how new signups are handled.",
        actions: h(Button, { variant: "secondary", onClick: load, disabled: busy }, "Refresh"),
      }),

      error ? h(Alert, { tone: "danger", title: "Action failed" }, error) : null,
      notice ? h(Alert, { tone: "success" }, notice) : null,

      h(Card, { title: "Approval policy", actions: h(Badge, { tone: approvalMode === "auto" ? "success" : "warning" }, modeLabel) },
        h(SettingRow, {
          label: "Automatic approval",
          description: "When enabled, the companion service creates new mailboxes immediately. Failed automatic attempts remain pending for review.",
        },
          h(Switch, {
            checked: approvalMode === "auto",
            onChange: (checked) => changeMode(checked ? "auto" : "manual"),
            disabled: busy,
          })
        ),
        h(Text, { size: "xs", tone: "muted" },
          "This setting affects new requests only. Existing pending requests still need an action."
        )
      ),

      h(Card, { title: "Email notifications" },
        h(Row, { gap: 2, align: "center" },
          h(Badge, { tone: notificationsEnabled ? "success" : "neutral" },
            notificationsEnabled ? "Enabled" : "Not configured"
          ),
          h(Text, { size: "sm", tone: "muted" },
            notificationsEnabled
              ? ("Notifications go to " + (notificationEmail || "the configured admin address") + ".")
              : "Configure SMTP_HOST and ADMIN_NOTIFICATION_EMAIL on the companion service to receive emails."
          )
        )
      ),

      h(Card, {
        title: "Pending requests",
        actions: h(Badge, { tone: requests.length ? "warning" : "success" }, String(requests.length)),
      },
        busy && !requests.length
          ? h(Text, { tone: "muted" }, "Loading requests...")
          : requests.length === 0
            ? h(Text, { tone: "muted" }, "There are no pending requests.")
            : h(Stack, { gap: 3 },
                ...requests.map((item) => h(Card, {
                  key: String(item.id),
                  title: item.address,
                  actions: h(Badge, { tone: "neutral" }, "#" + item.id),
                },
                  h(Stack, { gap: 2 },
                    h(Text, { size: "xs", tone: "muted" }, "Requested " + formatDate(item.createdAt)),
                    confirmation && confirmation.id === item.id
                      ? h(Alert, { tone: "warning", title: confirmation.action === "approve" ? "Create this mailbox?" : "Reject this request?" },
                          h(Text, { size: "sm" },
                            confirmation.action === "approve"
                              ? "The companion service will create this address in Stalwart using the password submitted by the applicant."
                              : "The request will be rejected and its encrypted password will be erased."
                          ),
                          h(Row, { gap: 2 },
                            h(Button, {
                              size: "sm",
                              onClick: () => finishAction(item, confirmation.action),
                              disabled: busyId === item.id,
                            }, confirmation.action === "approve" ? "Confirm approval" : "Confirm rejection"),
                            h(Button, {
                              size: "sm",
                              variant: "secondary",
                              onClick: () => setConfirmation(null),
                              disabled: busyId === item.id,
                            }, "Cancel")
                          )
                        )
                      : h(Row, { gap: 2 },
                          h(Button, {
                            size: "sm",
                            onClick: () => setConfirmation({ id: item.id, action: "approve" }),
                            disabled: busy || busyId !== null,
                          }, "Approve and create mailbox"),
                          h(Button, {
                            size: "sm",
                            variant: "secondary",
                            onClick: () => setConfirmation({ id: item.id, action: "reject" }),
                            disabled: busy || busyId !== null,
                          }, "Reject")
                        )
                  )
                ))
              )
      )
    )
  );
}

const slots = {
  "admin-plugin-page": {
    component: AdminPage,
  },
};

async function activate() {}

module.exports = { activate, slots };
