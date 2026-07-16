import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <div class="h-screen w-screen flex flex-col overflow-hidden bg-surface-950">
      <!-- Global Top Bar (visible on Home and Editor) -->
      <header class="glass header-gradient-border flex items-center justify-between px-5 py-3 z-50 shrink-0">
        <div class="flex items-center gap-3">
          <button *ngIf="isEditorRoute()" class="text-surface-400 hover:text-surface-200 text-sm mr-2"
              (click)="goHome()">← Workflows</button>
          <span class="text-xl logo-glow">⚡</span>
          <h1 class="text-base font-semibold tracking-tight">
            <span class="text-accent-light">UPIAA</span>
            <span class="text-surface-400 font-normal ml-1.5">Workflow Engine</span>
          </h1>
        </div>

        <div class="flex items-center gap-4">
          <div class="flex items-center gap-2 text-xs text-surface-500">
            <span class="inline-block w-2 h-2 rounded-full bg-success"></span>
            Engine Online
          </div>

          <!-- ── Global Theme Toggle ── -->
          <button
            class="theme-toggle-btn"
            (click)="toggleTheme()"
            [title]="isDarkMode ? 'Switch to Light Mode' : 'Switch to Dark Mode'"
            aria-label="Toggle light/dark theme">
            <span class="theme-toggle-icon">{{ isDarkMode ? '🌙' : '☀️' }}</span>
          </button>
        </div>
      </header>

      <!-- Router Outlet -->
      <main class="flex-1 overflow-hidden relative">
        <router-outlet></router-outlet>
      </main>
    </div>
  `,
})
export class AppComponent {
  isDarkMode = true;

  constructor(private router: Router) {
    // Restore saved theme preference on startup
    const saved = localStorage.getItem('UPIAA-theme');
    this.isDarkMode = saved !== 'light';
    this.applyTheme();
  }

  isEditorRoute(): boolean {
    return this.router.url.startsWith('/editor');
  }

  goHome(): void {
    this.router.navigate(['/']);
  }

  toggleTheme(): void {
    this.isDarkMode = !this.isDarkMode;
    this.applyTheme();
    localStorage.setItem('UPIAA-theme', this.isDarkMode ? 'dark' : 'light');
  }

  private applyTheme(): void {
    if (this.isDarkMode) {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', 'light');
    }
  }
}
