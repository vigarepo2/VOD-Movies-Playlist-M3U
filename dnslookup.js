const html = `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Subdomain Lookup</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    /* General Reset */
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: 'Inter', sans-serif;
      background-color: #f5faff;
      color: #333;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px;
      min-height: 100vh;
    }

    /* Header Style */
    h1 {
      font-size: 36px;
      font-weight: 700;
      color: #007bff;
      text-align: center;
      margin-bottom: 30px;
    }

    /* Search Bar */
    .search-bar {
      display: flex;
      align-items: center;
      gap: 10px;
      width: 100%;
      max-width: 600px;
      margin-bottom: 30px;
    }
    .search-bar input[type="text"] {
      flex-grow: 1;
      padding: 14px 20px;
      font-size: 16px;
      border: 2px solid #ddd;
      border-radius: 8px;
      outline: none;
      transition: border-color 0.3s ease, box-shadow 0.3s ease;
    }
    .search-bar input[type="text"]:focus {
      border-color: #007bff;
      box-shadow: 0 0 10px rgba(0, 123, 255, 0.2);
    }
    .search-bar button {
      padding: 14px 20px;
      font-size: 16px;
      font-weight: 600;
      color: white;
      background-color: #007bff;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: background-color 0.3s ease;
    }
    .search-bar button:hover {
      background-color: #0056b3;
    }

    /* Subdomain List */
    .subdomain-list {
      width: 100%;
      max-width: 600px;
      max-height: 400px;
      background-color: #ffffff;
      border-radius: 8px;
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
      overflow-y: auto;
      padding: 20px;
      margin-top: 20px;
    }
    .subdomain-list p {
      font-size: 16px;
      color: #555;
      padding: 8px 0;
      border-bottom: 1px solid #f0f0f0;
      word-wrap: break-word;
    }
    .subdomain-list p:last-child {
      border-bottom: none;
    }

    /* Scrollbar Styling */
    .subdomain-list::-webkit-scrollbar {
      width: 8px;
    }
    .subdomain-list::-webkit-scrollbar-thumb {
      background-color: #ccc;
      border-radius: 4px;
    }
    .subdomain-list::-webkit-scrollbar-thumb:hover {
      background-color: #aaa;
    }

    /* Responsive Design */
    @media (max-width: 768px) {
      h1 {
        font-size: 28px;
      }
      .search-bar input[type="text"] {
        font-size: 14px;
        padding: 12px 16px;
      }
      .search-bar button {
        font-size: 14px;
        padding: 12px 16px;
      }
      .subdomain-list p {
        font-size: 14px;
      }
    }
  </style>
</head>
<body>
  <h1>Subdomain Lookup</h1>
  {{content}}
</body>
</html>
`;

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname.substring(1); // Remove the leading slash
    const domainPattern = /^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

    // Handle API requests
    if (domainPattern.test(path)) {
      const domain = path;

      try {
        // Fetch subdomain data from crt.sh
        const crtshUrl = `https://crt.sh/?q=${encodeURIComponent(domain)}&output=json`;
        const response = await fetch(crtshUrl);

        if (!response.ok) {
          throw new Error("Error fetching subdomain data.");
        }

        const data = await response.json();
        const subdomains = [...new Set(data.map((entry) => entry.name_value))].sort();

        return new Response(JSON.stringify({ domain, subdomains }, null, 2), {
          headers: {
            "Content-Type": "application/json",
          },
        });
      } catch (err) {
        return new Response(
          JSON.stringify({ error: "Unable to fetch subdomain data.", details: err.message }, null, 2),
          {
            headers: { "Content-Type": "application/json" },
          }
        );
      }
    }

    // Handle UI requests
    if (url.pathname === "/lookup" && url.searchParams.has("domain")) {
      const domain = url.searchParams.get("domain");

      if (!domainPattern.test(domain)) {
        return new Response("Invalid domain name.", { status: 400 });
      }

      const crtshUrl = `https://crt.sh/?q=${encodeURIComponent(domain)}&output=json`;
      let response;
      try {
        response = await fetch(crtshUrl);
        if (!response.ok) {
          throw new Error("Error fetching subdomain data.");
        }
      } catch (err) {
        return new Response("Error retrieving data.", { status: 500 });
      }

      const data = await response.json();
      const subdomains = [...new Set(data.map((entry) => entry.name_value))].sort();

      const content = `
        <div class="search-bar">
          <form method="GET" action="/lookup">
            <input type="text" name="domain" placeholder="Enter domain name (e.g., example.com)" required />
            <button type="submit">Look Up</button>
          </form>
        </div>
        <div class="subdomain-list">
          ${subdomains.map((subdomain) => `<p>${subdomain}</p>`).join("")}
        </div>
      `;

      return new Response(html.replace("{{content}}", content), {
        headers: { "Content-Type": "text/html" },
      });
    }

    const homepageContent = `
        <div class="search-bar">
          <form method="GET" action="/lookup">
            <input type="text" name="domain" placeholder="Enter domain name (e.g., example.com)" required />
            <button type="submit">Look Up</button>
          </form>
        </div>
      `;

    return new Response(html.replace("{{content}}", homepageContent), {
      headers: { "Content-Type": "text/html" },
    });
  },
};
