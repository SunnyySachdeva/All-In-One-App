# TODO - Pending Tasks

## Uncompleted Features (from original list)

### High Priority
- [ ] **Lists Tab** - Add new tab with horizontal sub-tabs (To Do, Movies, Series, Books, Links, +)
- [ ] **Global Search** - Search functionality across tasks, notes, lists
- [x] **App Name** - Change page title and heading from "Workspace" to "Nexus" (Completed)

### Medium Priority
- [ ] **Neon Color Picker** - Dropdown for selecting pill color for new list items
- [ ] **Mini Media Player** - Fixed bar at top for controlling YouTube/Podcasts from any tab
- [x] **Favicon** - Add favicon with crystal ball emoji (Completed)
- [ ] **Sidebar Collapse** - Toggle button to collapse/expand left panel
- [ ] **Custom Lists** - '+' button to create custom lists with custom fields

### New Features
- [ ] **Learning Tab** - Bookmark important courses, Git repos, hacking links, Python links, etc.

## Completed
- [x] Make tabs draggable in left panel
- [x] Add slide-up animation for tab switching
- [x] Remove "LIVE" text from tab buttons
- [x] **Cache Feature** - Load YouTube feeds from local cache by default. Refresh button fetches new data
- [x] **App Name** - Changed from "Workspace" to "Nexus"
- [x] **Favicon** - Added crystal ball emoji favicon
- [x] **Responsive Notes Grid** - Updated to 1/2/4 columns based on screen size
- [x] **YouTube Cache Status** - Display cache/network source in status message
- [x] **Added Lists, Learning, Bookmarks tabs** - Added with 'Pending' badges to indicate future implementation

## New Features (Upcoming)
- [ ] **Dark Glass Theme** - Add a new dark glass theme with fancy black/gray look to theme selector
- [ ] **Enhanced Dashy Tab** - Rename to 'Self hosted stuff', add '+' button for dynamic quick links with popup for link text
- [ ] **Smart Terminal** - Remove hardcoded directory, use actual current directory, handle different OS types (Linux/Windows)
- [ ] **Settings Panel** - Add settings button with Ollama endpoint and OpenAI API key configuration, integrate with Videos tab summary feature

## Notes
- All tasks were partially implemented in a previous session but reverted to commit 52991bf
- The Lists tab and custom lists feature requires adding database tables and API endpoints
- Cache feature: Uses cache/youtube/ folder with JSON files, loads from cache by default, refresh button fetches new data
- Learning Tab: Add new tab with categorization (Courses, Repos, Hacking, Python, etc.) with add/edit/delete functionality
