import { create } from 'zustand';
import type { Asset, MaterialSearchResult } from '@/types/api';
import { loadPrefArray, savePref } from '@/services/storage/localPrefs';

type AssetTab = 'ai' | 'library' | 'history' | 'dub' | 'plugins';

interface AssetState {
  activeTab: AssetTab;
  assets: Asset[];
  searchResults: MaterialSearchResult[];
  history: Asset[];
  isLoading: boolean;
  searchQuery: string;
  uploadProgress: number | null;
  refreshCounter: number;

  // Actions
  setActiveTab: (tab: AssetTab) => void;
  setAssets: (assets: Asset[]) => void;
  setSearchResults: (results: MaterialSearchResult[]) => void;
  addToHistory: (asset: Asset) => void;
  setLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;
  setUploadProgress: (progress: number | null) => void;
  clearAssets: () => void;
}

export const useAssetStore = create<AssetState>((set) => ({
  activeTab: 'library',
  assets: [],
  searchResults: [],
  history: loadPrefArray<Asset>('assetHistory'),
  isLoading: false,
  searchQuery: '',
  uploadProgress: null,
  refreshCounter: 0,

  setActiveTab: (tab) => set({ activeTab: tab }),
  setAssets: (assets) => set({ assets }),
  setSearchResults: (results) => set({ searchResults: results }),

  addToHistory: (asset) =>
    set((state) => {
      const history = [asset, ...state.history.filter((a) => a.id !== asset.id)].slice(0, 50);
      savePref('assetHistory', history);
      return { history };
    }),

  setLoading: (loading) => set({ isLoading: loading }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setUploadProgress: (progress) => set({ uploadProgress: progress }),
  clearAssets: () => set((s) => ({ assets: [], searchResults: [], isLoading: false, uploadProgress: null, refreshCounter: s.refreshCounter + 1 })),
}));
