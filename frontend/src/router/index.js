import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
  { path: '/register', name: 'register', component: () => import('../views/Register.vue') },
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/devices/:deviceId',
    name: 'device-detail',
    component: () => import('../views/DeviceDetail.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/zones',
    name: 'zones',
    component: () => import('../views/Zones.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/provision',
    name: 'provision',
    component: () => import('../views/Provision.vue'),
    meta: { requiresAuth: true, requiresRole: 'admin' }
  },
  {
    path: '/rules',
    name: 'rules',
    component: () => import('../views/Rules.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/alerts',
    name: 'alerts',
    component: () => import('../views/Alerts.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/reminders',
    name: 'reminders',
    component: () => import('../views/Reminders.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/Settings.vue'),
    meta: { requiresAuth: true }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login' }
  }
  if (to.meta.requiresRole && auth.user?.role !== to.meta.requiresRole) {
    // Not a punishment -- just not this account's job. Send them
    // somewhere useful instead of an empty "forbidden" page.
    return { name: 'dashboard' }
  }
})

export default router
