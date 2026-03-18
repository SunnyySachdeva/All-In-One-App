# Nexus Dashboard Application

## Installation

### Docker
Pull and run the pre-built Docker image:
```bash
docker pull sunnysd22/nexus-dashboard
docker run -p 5105:5105 sunnysd22/nexus-dashboard
```

### Manual
1. Clone the repository:
```bash
git clone https://github.com/yourusername/nexus-dashboard.git
cd nexus-dashboard
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python app.py
```

4. Open your browser to http://localhost:5105

# TODO - Pending Tasks

## Uncompleted Features (from original list)

### High Priority
- [ ] **Lists Tab** - Add new tab with horizontal sub-tabs (To Do, Movies, Series, Books, Links, +)
- [ ] **Global Search** - Search functionality across tasks, notes, lists
- [ ] **App Name** - Change page title and heading from "Workspace" to "Nexus"

### Medium Priority
- [ ] **Neon Color Picker** - Dropdown for selecting pill color for new list items
- [ ] **Mini Media Player** - Fixed bar at top for controlling YouTube/Podcasts from any tab
- [ ] **Favicon** - Add favicon with badge for pending task count
- [ ] **Sidebar Collapse** - Toggle button to collapse/expand left panel
- [ ] **Custom Lists** - '+' button to create custom lists with custom fields

### New Features
- [ ] **Cache Feature** - Load YouTube, RSS, podcast feeds from local storage quickly. Fetch new feeds only with refresh button and periodically once a day
- [ ] **Learning Tab** - Bookmark important courses, Git repos, hacking links, Python links, etc.

## Completed
- [x] Make tabs draggable in left panel
- [x] Add slide-up animation for tab switching
- [x] Remove "LIVE" text from tab buttons

## Notes
- All tasks were partially implemented in a previous session but reverted to commit 52991bf
- The Lists tab and custom lists feature requires adding database tables and API endpoints
- Cache feature: Use localStorage/IndexedDB to store feeds with timestamps, refresh on demand and daily background sync
- Learning Tab: Add new tab with categorization (Courses, Repos, Hacking, Python, etc.) with add/edit/delete functionality


## Inbuilt Pomodoro
![Pomodoro](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20175931.png)
## Manage Tasks.
![Tasks](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20175910.png)
## Add youtube Channel/Channels.
![Videos](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20180043.png)
## Add your fav feeds and read on the same page.
![RSS Feeds](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20180016.png)
## Add Podcasts and play on the same page.
![Podcasts](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20175802.png)
## Simple Shell
![Shell](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20180245.png)
## Multiple color palettes to choose from
![Palettes](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20180428.png)
## Beautiful light mode
![light mode](https://github.com/SunnyySachdeva/All-In-One-App/blob/main/screenshots/Screenshot%202026-03-15%20180330.png)