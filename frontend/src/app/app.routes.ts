import { Routes } from '@angular/router';

export const routes: Routes = [
    {
        path: '',
        loadComponent: () =>
            import('./home/home.component').then(m => m.HomeComponent),
    },
    {
        path: 'editor',
        loadComponent: () =>
            import('./canvas/canvas.component').then(m => m.CanvasComponent),
    },
    {
        path: 'editor/:id',
        loadComponent: () =>
            import('./canvas/canvas.component').then(m => m.CanvasComponent),
    },
];
