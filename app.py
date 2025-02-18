from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory, jsonify, flash
import os, threading, time, urllib.parse, re, hashlib, platform, shutil
import requests, psutil, humanize
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.secret_key = "supersecretkey"

# Folder for downloads
UPLOAD_FOLDER = "temp_downloads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Global dictionaries for download tracking and file mappings
downloads_status = {}   # key: save_filename -> info about progress/status
file_mappings = {}      # custom URL mappings {save_filename: custom_path}
download_history = []   # store history of downloads (completed, failed etc.)
active_downloads = {}   # track active downloads (threads)
cancel_flags = {}       # flag to cancel downloads

def get_system_info():
    disk = psutil.disk_usage('/')
    memory = psutil.virtual_memory()
    return {
        'disk_total': format_size(disk.total),
        'disk_used': format_size(disk.used),
        'disk_free': format_size(disk.free),
        'disk_percent': disk.percent,
        'memory_total': format_size(memory.total),
        'memory_used': format_size(memory.used),
        'memory_free': format_size(memory.free),
        'memory_percent': memory.percent,
        'cpu_percent': psutil.cpu_percent(interval=1),
        'platform': platform.platform(),
        'python_version': platform.python_version(),
        'upload_folder_size': format_size(get_directory_size(UPLOAD_FOLDER)),
        'upload_file_count': len(os.listdir(UPLOAD_FOLDER))
    }

def get_directory_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total

def format_size(size):
    return humanize.naturalsize(size, binary=True)

def format_time(seconds):
    return humanize.naturaltime(seconds)

def get_file_hash(filepath):
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def check_duplicate_file(filepath):
    if not os.path.exists(filepath):
        return False
    new_hash = get_file_hash(filepath)
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename != os.path.basename(filepath):
            existing_file = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(existing_file):
                if get_file_hash(existing_file) == new_hash:
                    return True
    return False

def calculate_download_speed(start_time, downloaded):
    elapsed_time = time.time() - start_time
    if elapsed_time > 0:
        return downloaded / elapsed_time
    return 0

def estimate_time_remaining(total_size, downloaded, speed):
    if speed > 0:
        remaining_bytes = total_size - downloaded
        return remaining_bytes / speed
    return 0

def get_filename_from_url(url, response):
    try:
        if 'Content-Disposition' in response.headers:
            cd = response.headers['Content-Disposition']
            if 'filename=' in cd:
                filename = re.findall('filename="?([^"]+)"?', cd)[0]
                return urllib.parse.unquote(filename)
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        if path and '/' in path:
            return path.split('/')[-1]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if 'file' in query:
            return urllib.parse.unquote(query['file'][0])
    except Exception:
        pass
    return None

def download_file_async(url, save_filename, original_filename):
    try:
        start_time = time.time()
        response = requests.get(url, stream=True, allow_redirects=True)
        response.raise_for_status()

        filepath = os.path.join(UPLOAD_FOLDER, save_filename)
        total_size = int(response.headers.get('content-length', 0))
        block_size = 8192
        downloaded = 0
        active_downloads[save_filename] = {'start_time': start_time, 'total_size': total_size}

        with open(filepath, 'wb') as file:
            for data in response.iter_content(block_size):
                if cancel_flags.get(save_filename, False):
                    downloads_status[save_filename] = {
                        'status': 'cancelled',
                        'error': 'Download cancelled by user',
                        'original_name': original_filename,
                        'start_time': start_time
                    }
                    file.close()
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    active_downloads.pop(save_filename, None)
                    return
                file.write(data)
                downloaded += len(data)
                if total_size:
                    progress = int((downloaded / total_size) * 100)
                    speed = calculate_download_speed(start_time, downloaded)
                    eta = estimate_time_remaining(total_size, downloaded, speed)
                    downloads_status[save_filename] = {
                        'status': 'downloading',
                        'progress': progress,
                        'size': total_size,
                        'downloaded': downloaded,
                        'original_name': original_filename,
                        'formatted_size': format_size(total_size),
                        'formatted_downloaded': format_size(downloaded),
                        'speed': format_size(speed) + '/s',
                        'eta': format_time(eta) if eta else 'Calculating...',
                        'start_time': start_time
                    }

        # Finished download: check duplicate
        if check_duplicate_file(filepath):
            os.remove(filepath)
            downloads_status[save_filename] = {
                'status': 'duplicate',
                'error': 'File already exists',
                'original_name': original_filename,
                'start_time': start_time
            }
        else:
            downloads_status[save_filename] = {
                'status': 'completed',
                'progress': 100,
                'size': total_size,
                'downloaded': total_size,
                'original_name': original_filename,
                'formatted_size': format_size(total_size),
                'formatted_downloaded': format_size(total_size),
                'completion_time': format_time(time.time() - start_time),
                'start_time': start_time
            }
        # Update download history (most recent first)
        download_history.insert(0, {
            'file_id': save_filename,
            'name': original_filename,
            'status': downloads_status[save_filename]['status'],
            'completed': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        active_downloads.pop(save_filename, None)
    except Exception as e:
        downloads_status[save_filename] = {
            'status': 'failed',
            'error': str(e),
            'original_name': original_filename,
            'start_time': time.time()
        }
        download_history.insert(0, {
            'file_id': save_filename,
            'name': original_filename,
            'status': 'failed',
            'completed': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        active_downloads.pop(save_filename, None)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FDL Server</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
  <style>
    :root {
      --primary-color: #1a237e;
      --secondary-color: #0d47a1;
    }
    body {
      background-color: #f8f9fa;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      transition: background-color 0.3s, color 0.3s;
    }
    .dark-mode {
      background-color: #121212;
      color: #e0e0e0;
    }
    .navbar {
      background-color: var(--primary-color) !important;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .card {
      border: none;
      border-radius: 12px;
      box-shadow: 0 2px 15px rgba(0,0,0,0.08);
      margin-bottom: 20px;
      overflow: hidden;
    }
    .system-info {
      background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
      color: white;
      padding: 20px;
      border-radius: 12px;
      margin-bottom: 20px;
    }
    .system-info-item {
      padding: 10px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.1);
      margin-bottom: 10px;
    }
    .progress {
      height: 8px;
      border-radius: 4px;
      background-color: #e9ecef;
    }
    .progress-bar {
      background-color: var(--primary-color);
    }
    .download-item {
      background: white;
      padding: 20px;
      border-radius: 10px;
      margin-bottom: 15px;
      border: 1px solid #eee;
      transition: all 0.3s ease;
    }
    .download-item:hover {
      box-shadow: 0 5px 15px rgba(0,0,0,0.05);
    }
    .dark-mode .download-item {
      background: #1e1e1e;
      border: 1px solid #333;
    }
    .filename {
      font-size: 0.95rem;
      font-weight: 500;
      color: #2c3e50;
      word-break: break-word;
      margin-bottom: 10px;
    }
    .custom-url {
      font-family: monospace;
      background: #f8f9fa;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.85em;
      color: #666;
      word-break: break-all;
    }
    .dark-mode .custom-url {
      background: #333;
      color: #ccc;
    }
    .size-info, .speed-info {
      font-size: 0.85rem;
    }
    .speed-info {
      color: var(--primary-color);
      font-weight: 500;
    }
    .btn {
      border-radius: 6px;
      padding: 0.5rem 1rem;
    }
    .btn-sm {
      padding: 0.25rem 0.5rem;
    }
    .btn-primary {
      background-color: var(--primary-color);
      border-color: var(--primary-color);
    }
    .btn-primary:hover {
      background-color: var(--secondary-color);
      border-color: var(--secondary-color);
    }
    .dark-mode .btn-primary {
      background-color: #2196F3;
      border-color: #2196F3;
    }
    .dark-mode .btn-primary:hover {
      background-color: #1A237E;
      border-color: #1A237E;
    }
    .badge {
      font-size: 0.85rem;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
    }
    .badge-success {
      background-color: #2ecc71;
    }
    .badge-danger {
      background-color: #e74c3c;
    }
    .badge-warning {
      background-color: #f1c40f;
    }
    .badge-secondary {
      background-color: #95a5a6;
    }
    .badge-primary {
      background-color: var(--primary-color);
    }
    .dark-mode .badge-primary {
      background-color: #2196F3;
    }
    .modal-content {
      background-color: white;
      border: none;
      border-radius: 12px;
      box-shadow: 0 2px 15px rgba(0,0,0,0.08);
    }
    .dark-mode .modal-content {
      background-color: #1e1e1e;
      border: 1px solid #333;
    }
    .modal-header {
      background-color: var(--primary-color);
      color: white;
      padding: 20px;
      border-radius: 12px 12px 0 0;
    }
    .dark-mode .modal-header {
      background-color: #1a237e;
    }
    .modal-body {
      padding: 20px;
    }
    .modal-footer {
      padding: 20px;
      border-top: 1px solid #e9ecef;
    }
    .dark-mode .modal-footer {
      border-top: 1px solid #333;
    }
    .alert {
      border-radius: 6px;
      padding: 0.5rem 1rem;
    }
    .alert-dismissible .btn-close {
      padding: 0.5rem 1rem;
    }
    .dark-mode .alert {
      background-color: #333;
      color: #ccc;
    }
    .dark-mode .alert-dismissible .btn-close {
      color: #ccc;
    }
    .dark-mode .alert-dismissible .btn-close:hover {
      color: white;
    }
  </style>
</head>
<body>
  <!-- Navbar with brand and dark mode toggle -->
  <nav class="navbar navbar-dark mb-4">
    <div class="container">
      <a class="navbar-brand" href="/">
        <i class="fas fa-bolt me-2"></i>FDL Server
      </a>
      <button class="btn btn-outline-light" id="darkModeToggle" onclick="toggleDarkMode()">
        <i class="fas fa-moon"></i>
      </button>
    </div>
  </nav>
  
  <div class="container">
    <!-- System Information Panel -->
    <div class="system-info">
      <h5 class="mb-3"><i class="fas fa-server me-2"></i>System Information</h5>
      <div class="row">
        <div class="col-md-6">
          <div class="system-info-item">
            <div class="d-flex justify-content-between">
              <span>Disk Space:</span>
              <span>{{ system_info.disk_used }} / {{ system_info.disk_total }}</span>
            </div>
            <div class="progress mt-2">
              <div class="progress-bar" role="progressbar" style="width: {{ system_info.disk_percent }}%"></div>
            </div>
          </div>
          <div class="system-info-item">
            <div class="d-flex justify-content-between">
              <span>Memory:</span>
              <span>{{ system_info.memory_used }} / {{ system_info.memory_total }}</span>
            </div>
            <div class="progress mt-2">
              <div class="progress-bar" role="progressbar" style="width: {{ system_info.memory_percent }}%"></div>
            </div>
          </div>
        </div>
        <div class="col-md-6">
          <div class="system-info-item">
            <div><i class="fas fa-microchip me-2"></i>CPU Usage: {{ system_info.cpu_percent }}%</div>
          </div>
          <div class="system-info-item">
            <div><i class="fas fa-folder me-2"></i>Upload Folder: {{ system_info.upload_folder_size }}</div>
            <div><i class="fas fa-file me-2"></i>Files: {{ system_info.upload_file_count }}</div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Flash messages -->
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, message in messages %}
          <div class="alert alert-{{ category }} alert-dismissible fade show">
            {{ message }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
          </div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    
    <!-- New Download Form -->
    <div class="row">
      <div class="col-12">
        <div class="card">
          <div class="card-header bg-white py-3">
            <h5 class="mb-0"><i class="fas fa-download me-2"></i>New Download</h5>
          </div>
          <div class="card-body">
            <form method="post" class="row g-3">
              <div class="col-md-8 col-sm-12">
                <input type="url" class="form-control" name="url" placeholder="Enter download URL" required>
              </div>
              <div class="col-md-2 col-sm-6">
                <input type="text" class="form-control" name="custom_url" placeholder="Custom URL path">
              </div>
              <div class="col-md-2 col-sm-6">
                <button type="submit" class="btn btn-primary w-100">
                  <i class="fas fa-download me-2"></i>Download
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
      
      <!-- Downloads List -->
      <div class="col-12">
        <div class="card">
          <div class="card-header bg-white py-3">
            <h5 class="mb-0 d-inline-block"><i class="fas fa-list me-2"></i>Downloads</h5>
            <button class="btn btn-sm btn-outline-secondary float-end" onclick="clearDownloads()">
              <i class="fas fa-trash-alt"></i> Clear Finished
            </button>
          </div>
          <div class="card-body" id="downloads-list">
            {% for filename, info in downloads.items() %}
              <div class="download-item">
                <div class="d-flex flex-column flex-md-row justify-content-between align-items-start align-items-md-center mb-3">
                  <span class="filename">{{ info.get('original_name', filename) }}</span>
                  <div class="mt-2 mt-md-0">
                    {% if info.status in ['completed'] %}
                      <button class="btn btn-sm btn-outline-primary me-1" onclick="openRenameModal('{{ filename }}', '{{ info.get('original_name', filename)|e }}')">
                        <i class="fas fa-edit"></i>
                      </button>
                      <a href="{{ url_for('download_file', filename=filename) }}" class="btn btn-sm btn-success me-1">
                        <i class="fas fa-download"></i>
                      </a>
                      <button class="btn btn-sm btn-danger" onclick="openDeleteModal('{{ filename }}')">
                        <i class="fas fa-trash"></i>
                      </button>
                    {% elif info.status in ['downloading', 'starting'] %}
                      <button class="btn btn-sm btn-warning me-1" onclick="cancelDownload('{{ filename }}')">
                        <i class="fas fa-stop"></i> Cancel
                      </button>
                    {% endif %}
                  </div>
                </div>
                {% if info.status == 'completed' and filename in file_mappings %}
                  <div class="custom-url mb-3">
                    {{ request.host_url }}download/{{ file_mappings[filename] }}
                  </div>
                {% endif %}
                <div class="progress mb-2">
                  <div class="progress-bar {% if info.status == 'downloading' %}progress-bar-striped progress-bar-animated{% endif %}"
                       role="progressbar" style="width: {{ info.progress if info.progress else 0 }}%">
                  </div>
                </div>
                <div class="d-flex justify-content-between align-items-center">
                  <div>
                    <span class="size-info">
                      {% if info.formatted_downloaded %}
                        {{ info.formatted_downloaded }} / {{ info.formatted_size }}
                      {% endif %}
                    </span>
                    {% if info.status == 'downloading' %}
                      <span class="speed-info ms-2">
                        {{ info.speed }} &bull; {{ info.eta }}
                      </span>
                    {% endif %}
                    {% if info.status == 'completed' and info.completion_time %}
                      <span class="text-success ms-2">
                        <i class="fas fa-check-circle"></i> Completed {{ info.completion_time }}
                      </span>
                    {% endif %}
                  </div>
                  <span class="badge 
                    {% if info.status == 'completed' %}bg-success
                       {% elif info.status == 'failed' %}bg-danger
                       {% elif info.status == 'cancelled' %}bg-warning
                       {% elif info.status == 'duplicate' %}bg-secondary
                       {% else %}bg-primary{% endif %}">
                    {{ info.status|title }}
                  </span>
                </div>
              </div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div>
    
    <!-- Download History Panel -->
    <div class="row">
      <div class="col-12">
        <div class="card">
          <div class="card-header bg-white py-3">
            <h5 class="mb-0 d-inline-block"><i class="fas fa-history me-2"></i>Download History</h5>
          </div>
          <div class="card-body">
            <table class="table table-striped">
              <thead>
                <tr>
                  <th scope="col">File Name</th>
                  <th scope="col">Status</th>
                  <th scope="col">Completed</th>
                </tr>
              </thead>
              <tbody>
                {% for history in download_history %}
                  <tr>
                    <td>{{ history.name }}</td>
                    <td>
                      <span class="badge 
                        {% if history.status == 'completed' %}bg-success
                           {% elif history.status == 'failed' %}bg-danger
                           {% elif history.status == 'cancelled' %}bg-warning
                           {% elif history.status == 'duplicate' %}bg-secondary
                           {% else %}bg-primary{% endif %}">
                        {{ history.status|title }}
                      </span>
                    </td>
                    <td>{{ history.completed }}</td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Rename Modal -->
    <div class="modal fade" id="renameModal" tabindex="-1" aria-labelledby="renameModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <form id="renameForm">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="renameModalLabel">Rename File</h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
              <input type="hidden" id="rename-file-id" name="filename">
              <div class="mb-3">
                <label for="new-filename" class="form-label">New Filename:</label>
                <input type="text" class="form-control" id="new-filename" name="new_name" required>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
              <button type="submit" class="btn btn-primary">Rename</button>
            </div>
          </div>
        </form>
      </div>
    </div>
    
    <!-- Delete Confirmation Modal -->
    <div class="modal fade" id="deleteModal" tabindex="-1" aria-labelledby="deleteModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <form id="deleteForm">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="deleteModalLabel">Delete File</h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
              Are you sure you want to delete this file?
              <input type="hidden" id="delete-file-id" name="filename">
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
              <button type="submit" class="btn btn-danger">Delete</button>
            </div>
          </div>
        </form>
      </div>
    </div>
    
    <!-- jQuery and Bootstrap Bundle with Popper -->
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
      // Toggle dark mode
      function toggleDarkMode() {
        document.body.classList.toggle("dark-mode");
        const btn = document.getElementById("darkModeToggle");
        if(document.body.classList.contains("dark-mode")){
          btn.innerHTML = '<i class="fas fa-sun"></i>';
        } else {
          btn.innerHTML = '<i class="fas fa-moon"></i>';
        }
      }

      // Update downloads list every second (partial update)
      function updateDownloads(){
        $.get('/status', function(data){
          $('#downloads-list').load(location.href + ' #downloads-list>*');
        });
      }
      setInterval(updateDownloads, 1000);

      // Open rename modal and set current values
      function openRenameModal(filename, currentName){
        $('#rename-file-id').val(filename);
        $('#new-filename').val(currentName);
        var renameModal = new bootstrap.Modal(document.getElementById('renameModal'));
        renameModal.show();
      }
      $('#renameForm').submit(function(e){
        e.preventDefault();
        var filename = $('#rename-file-id').val();
        var newName = $('#new-filename').val();
        $.post('/rename/' + filename, { new_name: newName }, function(response){
          if(response.status === 'success'){
            location.reload();
          } else {
            alert('Error: ' + response.message);
          }
        });
      });

      // Open delete modal and set current file id
      function openDeleteModal(filename){
        $('#delete-file-id').val(filename);
        var deleteModal = new bootstrap.Modal(document.getElementById('deleteModal'));
        deleteModal.show();
      }
      $('#deleteForm').submit(function(e){
        e.preventDefault();
        var filename = $('#delete-file-id').val();
        $.post('/delete/' + filename, function(response){
          if(response.status === 'success'){
            location.reload();
          } else {
            alert('Error: ' + response.message);
          }
        });
      });

      // Cancel a download
      function cancelDownload(filename){
        if(confirm('Are you sure you want to cancel this download?')){
          $.post('/cancel/' + filename, function(response){
            if(response.status === 'success'){
              location.reload();
            } else {
              alert('Error: ' + response.message);
            }
          });
        }
      }

      // Clear finished downloads from the list
      function clearDownloads(){
        if(confirm('Clear finished downloads from list?')){
          $.post('/clear', function(response){
            if(response.status === 'success'){
              location.reload();
            } else {
              alert('Error clearing downloads');
            }
          });
        }
      }
    </script>
  </body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        custom_url = request.form.get("custom_url")
        if url:
            try:
                response = requests.head(url, allow_redirects=True)
                original_filename = get_filename_from_url(url, response) or f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                save_filename = secure_filename(original_filename)
                if custom_url:
                    file_mappings[save_filename] = custom_url
                # Start download in a separate thread.
                thread = threading.Thread(
                    target=download_file_async,
                    args=(url, save_filename, original_filename)
                )
                thread.daemon = True
                thread.start()
                downloads_status[save_filename] = {
                    'status': 'starting',
                    'progress': 0,
                    'size': 0,
                    'downloaded': 0,
                    'original_name': original_filename
                }
                flash("Download started!", "success")
            except Exception as e:
                flash(f"Error: {str(e)}", "danger")
        return redirect(url_for("index"))
    return render_template_string(HTML_TEMPLATE, downloads=downloads_status, file_mappings=file_mappings, download_history=download_history, system_info=get_system_info())

@app.route("/status")
def get_status():
    return jsonify({'downloads': downloads_status, 'system_info': get_system_info()})

@app.route("/download/<path:filename>")
def download_file(filename):
    for real_filename, custom_url in file_mappings.items():
        if custom_url == filename:
            return send_from_directory(UPLOAD_FOLDER, real_filename, as_attachment=True)
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

@app.route("/rename/<filename>", methods=["POST"])
def rename_file(filename):
    new_name = request.form.get("new_name")
    if not new_name:
        return jsonify({"status": "error", "message": "No new name provided"})
    try:
        old_path = os.path.join(UPLOAD_FOLDER, filename)
        new_filename = secure_filename(new_name)
        new_path = os.path.join(UPLOAD_FOLDER, new_filename)
        os.rename(old_path, new_path)
        if filename in downloads_status:
            downloads_status[new_filename] = downloads_status.pop(filename)
            downloads_status[new_filename]['original_name'] = new_name
        if filename in file_mappings:
            file_mappings[new_filename] = file_mappings.pop(filename)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/delete/<filename>", methods=["POST"])
def delete_file(filename):
    try:
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            downloads_status.pop(filename, None)
            file_mappings.pop(filename, None)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/cancel/<filename>", methods=["POST"])
def cancel_download(filename):
    cancel_flags[filename] = True
    return jsonify({"status": "success"})

@app.route("/clear", methods=["POST"])
def clear_downloads():
    cleared = []
    for filename, info in list(downloads_status.items()):
        if info.get("status") in ["completed", "failed", "cancelled", "duplicate"]:
            downloads_status.pop(filename, None)
            file_mappings.pop(filename, None)
            cleared.append(filename)
    return jsonify({"status": "success", "cleared": cleared})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
