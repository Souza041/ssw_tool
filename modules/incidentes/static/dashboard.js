(function () {
    'use strict';

    var app = document.getElementById('incidentsApp');

    if (!app) {
        return;
    }

    var apiUrl = app.getAttribute('data-api-url');
    var statusUrl = app.getAttribute('data-status-url');
    var cacheUrl = app.getAttribute('data-cache-url');

    var dashboardData = null;
    var toastTimer = null;

    var chartInstances = {
        dailyEvolution: null,
        debitStatus: null
    };

    var elements = {
        loading: document.getElementById(
            'dashboardLoading'
        ),

        error: document.getElementById(
            'dashboardError'
        ),

        errorMessage: document.getElementById(
            'dashboardErrorMessage'
        ),

        content: document.getElementById(
            'dashboardContent'
        ),

        filtersForm: document.getElementById(
            'dashboardFiltersForm'
        ),

        clearFiltersButton: document.getElementById(
            'clearFiltersButton'
        ),

        retryButton: document.getElementById(
            'retryDashboardButton'
        ),

        reloadCacheButton: document.getElementById(
            'reloadCacheButton'
        ),

        snapshotStatus: document.getElementById(
            'snapshotStatus'
        ),

        snapshotIndicator: document.getElementById(
            'snapshotIndicator'
        ),

        snapshotName: document.getElementById(
            'snapshotName'
        ),

        startDate: document.getElementById(
            'filterStartDate'
        ),

        endDate: document.getElementById(
            'filterEndDate'
        ),

        groups: document.getElementById(
            'filterGroups'
        ),

        clients: document.getElementById(
            'filterClients'
        ),

        units: document.getElementById(
            'filterUnits'
        ),

        occurrences: document.getElementById(
            'filterOccurrences'
        ),

        debitStatus: document.getElementById(
            'filterDebitStatus'
        ),

        debitRules: document.getElementById(
            'filterDebitRules'
        ),

        products: document.getElementById(
            'filterProducts'
        ),

        metaUpdatedAt: document.getElementById(
            'metaUpdatedAt'
        ),

        metaTotalBefore: document.getElementById(
            'metaTotalBefore'
        ),

        metaTotalAfter: document.getElementById(
            'metaTotalAfter'
        ),

        metaFiltersStatus: document.getElementById(
            'metaFiltersStatus'
        ),

        kpiTotalCtrcs: document.getElementById(
            'kpiTotalCtrcs'
        ),

        kpiCargoValue: document.getElementById(
            'kpiCargoValue'
        ),

        kpiDebits: document.getElementById(
            'kpiDebits'
        ),

        kpiDebitsPercentage: document.getElementById(
            'kpiDebitsPercentage'
        ),

        kpiPending: document.getElementById(
            'kpiPending'
        ),

        kpiPendingPercentage: document.getElementById(
            'kpiPendingPercentage'
        ),

        kpiNonDebits: document.getElementById(
            'kpiNonDebits'
        ),

        kpiWithoutHistory: document.getElementById(
            'kpiWithoutHistory'
        ),

        kpiClients: document.getElementById(
            'kpiClients'
        ),

        kpiProducts: document.getElementById(
            'kpiProducts'
        ),

        dailyEvolutionChart: document.getElementById(
            'dailyEvolutionChart'
        ),

        debitStatusChart: document.getElementById(
            'debitStatusChart'
        ),

        occurrencesRankingChart: document.getElementById(
            'occurrencesRankingChart'
        ),

        clientGroupsChart: document.getElementById(
            'clientGroupsChart'
        ),

        unitsRankingChart: document.getElementById(
            'unitsRankingChart'
        ),

        productsRankingChart: document.getElementById(
            'productsRankingChart'
        ),

        rulesTableBody: document.getElementById(
            'rulesTableBody'
        ),

        rulesCounter: document.getElementById(
            'rulesCounter'
        ),

        attentionTableBody: document.getElementById(
            'attentionTableBody'
        ),

        attentionCounter: document.getElementById(
            'attentionCounter'
        ),

        toast: document.getElementById(
            'dashboardToast'
        ),

        toastMessage: document.getElementById(
            'dashboardToastMessage'
        )
    };


    // ==================================================
    // Inicialização
    // ==================================================

    function initialize() {
        bindEvents();
        loadModuleStatus();
        loadDashboard();
    }


    function bindEvents() {
        if (elements.filtersForm) {
            elements.filtersForm.addEventListener(
                'submit',
                function (event) {
                    event.preventDefault();
                    loadDashboard();
                }
            );
        }

        if (elements.clearFiltersButton) {
            elements.clearFiltersButton.addEventListener(
                'click',
                clearFilters
            );
        }

        if (elements.retryButton) {
            elements.retryButton.addEventListener(
                'click',
                function () {
                    loadModuleStatus();
                    loadDashboard();
                }
            );
        }

        if (elements.reloadCacheButton) {
            elements.reloadCacheButton.addEventListener(
                'click',
                reloadCache
            );
        }
    }


    // ==================================================
    // Requisições
    // ==================================================

    function requestJson(url, options) {
        return fetch(url, options || {})
            .then(function (response) {
                return response.text()
                    .then(function (text) {
                        var payload = {};

                        if (text) {
                            try {
                                payload = JSON.parse(text);
                            } catch (error) {
                                payload = {
                                    detail: text
                                };
                            }
                        }

                        if (!response.ok) {
                            throw new Error(
                                payload.detail ||
                                payload.message ||
                                (
                                    'Erro HTTP ' +
                                    response.status
                                )
                            );
                        }

                        return payload;
                    });
            });
    }


    function loadModuleStatus() {
        if (!statusUrl) {
            return Promise.resolve();
        }

        return requestJson(statusUrl)
            .then(function (data) {
                updateSnapshotStatus(data);
            })
            .catch(function () {
                setSnapshotUnavailable();
            });
    }


    function loadDashboard() {
        setLoadingState();

        requestJson(
            buildDashboardUrl()
        )
            .then(function (data) {
                if (
                    data &&
                    data.success === false
                ) {
                    throw new Error(
                        data.message ||
                        'O dashboard não pôde ser carregado.'
                    );
                }

                dashboardData = data;

                renderDashboard(
                    data || {}
                );

                setContentState();
            })
            .catch(function (error) {
                setErrorState(
                    error.message
                );
            });
    }


    function reloadCache() {
        if (!cacheUrl) {
            return;
        }

        setButtonLoading(
            elements.reloadCacheButton,
            true,
            'Atualizando...'
        );

        requestJson(
            cacheUrl,
            {
                method: 'POST',
                headers: {
                    Accept: 'application/json'
                }
            }
        )
            .then(function (data) {
                showToast(
                    data.message ||
                    'Dados atualizados com sucesso.',
                    'success'
                );

                return loadModuleStatus()
                    .then(function () {
                        loadDashboard();
                    });
            })
            .catch(function (error) {
                showToast(
                    error.message ||
                    'Não foi possível atualizar os dados.',
                    'error'
                );
            })
            .finally(function () {
                setButtonLoading(
                    elements.reloadCacheButton,
                    false
                );
            });
    }


    // ==================================================
    // URL e filtros
    // ==================================================

    function buildDashboardUrl() {
        var parameters = new URLSearchParams();

        appendInputValue(
            parameters,
            'data_inicial',
            elements.startDate
        );

        appendInputValue(
            parameters,
            'data_final',
            elements.endDate
        );

        appendSelectValues(
            parameters,
            'grupos_clientes',
            elements.groups
        );

        appendSelectValues(
            parameters,
            'clientes',
            elements.clients
        );

        appendSelectValues(
            parameters,
            'unidades',
            elements.units
        );

        appendSelectValues(
            parameters,
            'codigos_ocorrencia',
            elements.occurrences
        );

        appendSelectValues(
            parameters,
            'status_debito',
            elements.debitStatus
        );

        appendSelectValues(
            parameters,
            'regras_debito',
            elements.debitRules
        );

        appendSelectValues(
            parameters,
            'produtos',
            elements.products
        );

        var query = parameters.toString();

        return query
            ? apiUrl + '?' + query
            : apiUrl;
    }


    function appendInputValue(
        parameters,
        name,
        input
    ) {
        if (!input || !input.value) {
            return;
        }

        parameters.append(
            name,
            input.value
        );
    }


    function appendSelectValues(
        parameters,
        name,
        select
    ) {
        if (!select) {
            return;
        }

        getSelectedValues(select)
            .forEach(function (value) {
                parameters.append(
                    name,
                    value
                );
            });
    }


    function getSelectedValues(select) {
        if (!select) {
            return [];
        }

        return Array.prototype
            .slice.call(
                select.selectedOptions || []
            )
            .map(function (option) {
                return option.value;
            })
            .filter(function (value) {
                return value !== '';
            });
    }


    function clearFilters() {
        if (elements.filtersForm) {
            elements.filtersForm.reset();
        }

        [
            elements.groups,
            elements.clients,
            elements.units,
            elements.occurrences,
            elements.debitStatus,
            elements.debitRules,
            elements.products
        ].forEach(
            clearMultipleSelect
        );

        loadDashboard();
    }


    function clearMultipleSelect(select) {
        if (!select) {
            return;
        }

        Array.prototype
            .slice.call(select.options)
            .forEach(function (option) {
                option.selected = false;
            });
    }


    // ==================================================
    // Renderização principal
    // ==================================================

    function renderDashboard(data) {
        renderFilterOptions(
            data.filter_options || {}
        );

        restoreAppliedFilters(
            data.applied_filters || {}
        );

        renderMeta(
            data.meta || {}
        );

        renderKpis(
            data.kpis || {}
        );

        renderCharts(
            data.charts || {}
        );

        renderRules(
            data.rules || []
        );

        renderAttentionRecords(
            data.attention_records || []
        );
    }


    // ==================================================
    // Filtros
    // ==================================================

    function renderFilterOptions(options) {
        var selected = captureSelectedFilters();

        populateSelect(
            elements.groups,
            options.grupos_clientes || [],
            selected.grupos_clientes
        );

        populateSelect(
            elements.clients,
            options.clientes || [],
            selected.clientes
        );

        populateSelect(
            elements.units,
            options.unidades || [],
            selected.unidades
        );

        populateSelect(
            elements.occurrences,
            options.codigos_ocorrencia || [],
            selected.codigos_ocorrencia
        );

        populateSelect(
            elements.debitStatus,
            options.status_debito || [],
            selected.status_debito
        );

        populateSelect(
            elements.debitRules,
            options.regras_debito || [],
            selected.regras_debito
        );

        populateSelect(
            elements.products,
            options.produtos || [],
            selected.produtos
        );
    }


    function captureSelectedFilters() {
        return {
            grupos_clientes:
                getSelectedValues(
                    elements.groups
                ),

            clientes:
                getSelectedValues(
                    elements.clients
                ),

            unidades:
                getSelectedValues(
                    elements.units
                ),

            codigos_ocorrencia:
                getSelectedValues(
                    elements.occurrences
                ),

            status_debito:
                getSelectedValues(
                    elements.debitStatus
                ),

            regras_debito:
                getSelectedValues(
                    elements.debitRules
                ),

            produtos:
                getSelectedValues(
                    elements.products
                )
        };
    }


    function populateSelect(
        select,
        options,
        selectedValues
    ) {
        if (!select) {
            return;
        }

        var selectedMap = {};

        (selectedValues || [])
            .forEach(function (value) {
                selectedMap[
                    String(value)
                ] = true;
            });

        select.innerHTML = '';

        if (!Array.isArray(options)) {
            return;
        }

        options.forEach(function (item) {
            var value;
            var label;
            var count;

            if (
                item !== null &&
                typeof item === 'object'
            ) {
                value = item.value;
                label = item.label;
                count = item.count;
            } else {
                value = item;
                label = item;
                count = null;
            }

            if (
                value === null ||
                typeof value === 'undefined'
            ) {
                return;
            }

            var option = document.createElement(
                'option'
            );

            option.value = String(value);

            option.textContent =
                formatFilterLabel(
                    label || value,
                    count
                );

            option.selected = Boolean(
                selectedMap[
                    String(value)
                ]
            );

            select.appendChild(option);
        });
    }


    function formatFilterLabel(
        label,
        count
    ) {
        if (
            count === null ||
            typeof count === 'undefined'
        ) {
            return String(label);
        }

        return (
            String(label) +
            ' (' +
            formatInteger(count) +
            ')'
        );
    }


    function restoreAppliedFilters(filters) {
        if (!filters) {
            return;
        }

        if (elements.startDate) {
            elements.startDate.value =
                normalizeDateInputValue(
                    filters.data_inicial
                );
        }

        if (elements.endDate) {
            elements.endDate.value =
                normalizeDateInputValue(
                    filters.data_final
                );
        }

        restoreSelectValues(
            elements.groups,
            filters.grupos_clientes
        );

        restoreSelectValues(
            elements.clients,
            filters.clientes
        );

        restoreSelectValues(
            elements.units,
            filters.unidades
        );

        restoreSelectValues(
            elements.occurrences,
            filters.codigos_ocorrencia
        );

        restoreSelectValues(
            elements.debitStatus,
            filters.status_debito
        );

        restoreSelectValues(
            elements.debitRules,
            filters.regras_debito
        );

        restoreSelectValues(
            elements.products,
            filters.produtos
        );
    }


    function restoreSelectValues(
        select,
        values
    ) {
        if (
            !select ||
            !Array.isArray(values)
        ) {
            return;
        }

        var valueMap = {};

        values.forEach(function (value) {
            valueMap[
                String(value)
            ] = true;
        });

        Array.prototype
            .slice.call(select.options)
            .forEach(function (option) {
                option.selected = Boolean(
                    valueMap[option.value]
                );
            });
    }


    // ==================================================
    // Metadados
    // ==================================================

    function renderMeta(meta) {
        setText(
            elements.metaUpdatedAt,
            formatDateTime(
                meta.source_updated_at ||
                meta.generated_at
            )
        );

        setText(
            elements.metaTotalBefore,
            formatInteger(
                meta.total_before_filters
            )
        );

        setText(
            elements.metaTotalAfter,
            formatInteger(
                meta.total_after_filters
            )
        );

        setText(
            elements.metaFiltersStatus,
            meta.filters_applied
                ? 'Filtros aplicados'
                : 'Sem filtros'
        );

        if (
            meta.source_name &&
            elements.snapshotName
        ) {
            elements.snapshotName.textContent =
                meta.source_name;
        }
    }


    // ==================================================
    // KPIs
    // ==================================================

    function renderKpis(kpis) {
        setText(
            elements.kpiTotalCtrcs,
            formatInteger(
                kpis.total_ctrcs
            )
        );

        setText(
            elements.kpiCargoValue,
            formatCurrency(
                kpis.valor_total_carga
            )
        );

        setText(
            elements.kpiDebits,
            formatInteger(
                kpis.total_debitos
            )
        );

        setText(
            elements.kpiDebitsPercentage,
            formatPercentage(
                kpis.percentual_debitos
            ) + ' dos registros'
        );

        setText(
            elements.kpiPending,
            formatInteger(
                kpis.total_pendentes
            )
        );

        setText(
            elements.kpiPendingPercentage,
            formatPercentage(
                kpis.percentual_pendentes
            ) + ' dos registros'
        );

        setText(
            elements.kpiNonDebits,
            formatInteger(
                kpis.total_nao_debitos
            )
        );

        setText(
            elements.kpiWithoutHistory,
            formatInteger(
                kpis.total_sem_historico
            )
        );

        setText(
            elements.kpiClients,
            formatInteger(
                kpis.clientes_distintos
            )
        );

        setText(
            elements.kpiProducts,
            formatInteger(
                kpis.produtos_distintos
            )
        );
    }


    // ==================================================
    // Gráficos
    // ==================================================

    function renderCharts(charts) {
        renderDailyEvolutionChart(
            getChartData(
                charts,
                [
                    'evolucao_diaria',
                    'daily_evolution'
                ]
            )
        );

        renderDebitStatusChart(
            getChartData(
                charts,
                [
                    'status_debitos',
                    'status_debito',
                    'debit_status'
                ]
            )
        );

        renderRankingList(
            elements.occurrencesRankingChart,
            getChartData(
                charts,
                [
                    'ranking_ocorrencias',
                    'ocorrencias',
                    'occurrences_ranking'
                ]
            ),
            {
                labelKeys: [
                    'codigo_ocorrencia',
                    'ocorrencia',
                    'codigo',
                    'categoria',
                    'label'
                ],
                valueKeys: [
                    'quantidade',
                    'total',
                    'count',
                    'value'
                ],
                maxItems: 10
            }
        );

        renderRankingList(
            elements.clientGroupsChart,
            getChartData(
                charts,
                [
                    'ranking_grupos_clientes',
                    'grupos_clientes',
                    'client_groups'
                ]
            ),
            {
                labelKeys: [
                    'grupo_cliente',
                    'grupo',
                    'categoria',
                    'nome',
                    'label'
                ],
                valueKeys: [
                    'quantidade',
                    'total',
                    'count',
                    'value'
                ],
                maxItems: 10
            }
        );

        renderRankingList(
            elements.unitsRankingChart,
            getChartData(
                charts,
                [
                    'ranking_unidades',
                    'unidades',
                    'units_ranking'
                ]
            ),
            {
                labelKeys: [
                    'unidade',
                    'filial',
                    'categoria',
                    'nome',
                    'label'
                ],
                valueKeys: [
                    'quantidade',
                    'total',
                    'count',
                    'value'
                ],
                maxItems: 10
            }
        );

        renderRankingList(
            elements.productsRankingChart,
            getChartData(
                charts,
                [
                    'ranking_produtos',
                    'produtos',
                    'products_ranking'
                ]
            ),
            {
                labelKeys: [
                    'produto',
                    'produto_predominante',
                    'descricao',
                    'categoria',
                    'nome',
                    'label'
                ],
                valueKeys: [
                    'quantidade',
                    'total',
                    'count',
                    'value'
                ],
                maxItems: 10
            }
        );
    }


    function getChartData(
        charts,
        possibleKeys
    ) {
        var index;

        for (
            index = 0;
            index < possibleKeys.length;
            index += 1
        ) {
            var key = possibleKeys[index];

            if (
                charts &&
                Object.prototype.hasOwnProperty.call(
                    charts,
                    key
                )
            ) {
                return normalizeChartData(
                    charts[key]
                );
            }
        }

        return [];
    }


    function normalizeChartData(data) {
        if (!data) {
            return [];
        }

        if (Array.isArray(data)) {
            return data;
        }

        if (Array.isArray(data.rows)) {
            return data.rows;
        }

        if (Array.isArray(data.items)) {
            return data.items;
        }

        if (Array.isArray(data.data)) {
            return data.data;
        }

        if (
            Array.isArray(data.series) &&
            data.series.length === 1
        ) {
            var firstSeries =
                data.series[0];

            if (
                firstSeries &&
                Array.isArray(
                    firstSeries.data
                )
            ) {
                return firstSeries.data;
            }

            if (
                firstSeries &&
                Array.isArray(
                    firstSeries.rows
                )
            ) {
                return firstSeries.rows;
            }
        }

        if (Array.isArray(data.values)) {
            if (Array.isArray(data.labels)) {
                return data.values.map(
                    function (value, index) {
                        return {
                            label:
                                data.labels[index] ||
                                'Não informado',
                            value: value
                        };
                    }
                );
            }

            return data.values;
        }

        return [];
    }


    function normalizeRows(
        rows,
        labelKeys,
        valueKeys
    ) {
        if (!Array.isArray(rows)) {
            return [];
        }

        return rows
            .map(function (row) {
                if (
                    row === null ||
                    typeof row === 'undefined'
                ) {
                    return null;
                }

                if (
                    typeof row !== 'object'
                ) {
                    return {
                        label: String(row),
                        value: 0
                    };
                }

                var label = getFirstValue(
                    row,
                    (labelKeys || []).concat([
                        'label',
                        'categoria',
                        'category',
                        'nome',
                        'descricao',
                        'produto',
                        'cliente',
                        'grupo',
                        'grupo_cliente',
                        'unidade',
                        'filial',
                        'codigo',
                        'codigo_ocorrencia',
                        'ocorrencia',
                        'status',
                        'data',
                        'periodo'
                    ])
                );

                var value = getFirstValue(
                    row,
                    (valueKeys || []).concat([
                        'value',
                        'valor',
                        'quantidade',
                        'total',
                        'count',
                        'total_registros',
                        'total_ocorrencias',
                        'numero_registros'
                    ])
                );

                return {
                    label: String(
                        label === null ||
                        typeof label === 'undefined' ||
                        label === ''
                            ? 'Não informado'
                            : label
                    ),
                    value: parseNumber(value)
                };
            })
            .filter(function (item) {
                return item !== null;
            });
    }


    // ==================================================
    // Evolução diária com ApexCharts
    // ==================================================

    function renderDailyEvolutionChart(rows) {
        if (!elements.dailyEvolutionChart) {
            return;
        }

        destroyChart(
            'dailyEvolution'
        );

        var normalized = normalizeRows(
            rows,
            [
                'data',
                'date',
                'periodo',
                'categoria',
                'label'
            ],
            [
                'quantidade',
                'total',
                'total_registros',
                'count',
                'value'
            ]
        );

        if (!normalized.length) {
            renderEmptyChart(
                elements.dailyEvolutionChart
            );
            return;
        }

        if (typeof ApexCharts === 'undefined') {
            renderChartLibraryError(
                elements.dailyEvolutionChart
            );
            return;
        }

        var categories = normalized.map(
            function (item) {
                return formatShortChartLabel(
                    item.label
                );
            }
        );

        var values = normalized.map(
            function (item) {
                return item.value;
            }
        );

        var options = {
            chart: {
                type: 'area',
                height: 310,
                fontFamily:
                    'Inter, system-ui, sans-serif',
                toolbar: {
                    show: false
                },
                zoom: {
                    enabled: false
                },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 450
                }
            },

            series: [
                {
                    name: 'Incidentes',
                    data: values
                }
            ],

            colors: [
                '#2f5edb'
            ],

            stroke: {
                width: 2.5,
                curve: 'smooth'
            },

            fill: {
                type: 'gradient',
                gradient: {
                    shadeIntensity: 1,
                    opacityFrom: 0.24,
                    opacityTo: 0.02,
                    stops: [
                        0,
                        90,
                        100
                    ]
                }
            },

            dataLabels: {
                enabled:
                    normalized.length <= 31,
                offsetY: -6,
                style: {
                    fontSize: '10px',
                    fontWeight: 600,
                    colors: [
                        '#526078'
                    ]
                },
                background: {
                    enabled: false
                }
            },

            markers: {
                size:
                    normalized.length <= 40
                        ? 3.5
                        : 0,

                strokeWidth: 2,
                strokeColors: '#ffffff',
                hover: {
                    size: 6
                }
            },

            grid: {
                borderColor: '#e8edf4',
                strokeDashArray: 0,
                padding: {
                    top: 14,
                    right: 12,
                    bottom: 0,
                    left: 8
                }
            },

            xaxis: {
                categories: categories,
                tickAmount:
                    Math.min(
                        categories.length,
                        10
                    ),

                labels: {
                    rotate: 0,
                    hideOverlappingLabels: true,
                    trim: true,
                    style: {
                        colors: '#8692a6',
                        fontSize: '11px'
                    }
                },

                axisBorder: {
                    show: false
                },

                axisTicks: {
                    show: false
                },

                tooltip: {
                    enabled: false
                }
            },

            yaxis: {
                min: 0,

                forceNiceScale: true,

                labels: {
                    formatter: function (value) {
                        return formatInteger(value);
                    },

                    style: {
                        colors: '#8692a6',
                        fontSize: '11px'
                    }
                }
            },

            tooltip: {
                shared: false,
                intersect: false,

                y: {
                    formatter: function (value) {
                        return (
                            formatInteger(value) +
                            ' registros'
                        );
                    }
                }
            },

            noData: {
                text: 'Nenhum dado disponível.'
            }
        };

        chartInstances.dailyEvolution =
            new ApexCharts(
                elements.dailyEvolutionChart,
                options
            );

        chartInstances.dailyEvolution.render();
    }


    // ==================================================
    // Status dos débitos com ApexCharts
    // ==================================================

    function renderDebitStatusChart(rows) {
        if (!elements.debitStatusChart) {
            return;
        }

        destroyChart(
            'debitStatus'
        );

        var normalized = normalizeRows(
            rows,
            [
                'status',
                'status_debito',
                'categoria',
                'label'
            ],
            [
                'quantidade',
                'total',
                'total_registros',
                'count',
                'value'
            ]
        );

        if (!normalized.length) {
            renderEmptyChart(
                elements.debitStatusChart
            );
            return;
        }

        if (typeof ApexCharts === 'undefined') {
            renderChartLibraryError(
                elements.debitStatusChart
            );
            return;
        }

        var labels = normalized.map(
            function (item) {
                return formatStatus(
                    item.label
                );
            }
        );

        var values = normalized.map(
            function (item) {
                return item.value;
            }
        );

        var total = values.reduce(
            function (sum, value) {
                return sum + value;
            },
            0
        );

        var colors = normalized.map(
            function (item) {
                return getStatusColor(
                    item.label
                );
            }
        );

        var options = {
            chart: {
                type: 'donut',
                height: 300,
                fontFamily:
                    'Inter, system-ui, sans-serif',
                toolbar: {
                    show: false
                },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 450
                }
            },

            series: values,

            labels: labels,

            colors: colors,

            stroke: {
                width: 3,
                colors: [
                    '#ffffff'
                ]
            },

            dataLabels: {
                enabled: false
            },

            legend: {
                show: true,
                position: 'right',
                horizontalAlign: 'center',

                fontSize: '12px',
                fontWeight: 500,

                labels: {
                    colors: '#526078'
                },

                markers: {
                    width: 8,
                    height: 8,
                    radius: 8
                },

                formatter: function (
                    seriesName,
                    opts
                ) {
                    var value =
                        opts.w.globals
                            .series[
                                opts.seriesIndex
                            ];

                    var percentage =
                        total > 0
                            ? (
                                value /
                                total
                            ) * 100
                            : 0;

                    return (
                        seriesName +
                        '  ' +
                        formatInteger(value) +
                        ' · ' +
                        formatPercentage(
                            percentage
                        )
                    );
                }
            },

            plotOptions: {
                pie: {
                    donut: {
                        size: '70%',

                        labels: {
                            show: true,

                            name: {
                                show: true,
                                offsetY: 22,
                                color: '#8692a6',
                                fontSize: '11px',
                                formatter: function () {
                                    return 'registros';
                                }
                            },

                            value: {
                                show: true,
                                offsetY: -12,
                                color: '#172033',
                                fontSize: '22px',
                                fontWeight: 700,
                                formatter: function () {
                                    return formatInteger(
                                        total
                                    );
                                }
                            },

                            total: {
                                show: false
                            }
                        }
                    }
                }
            },

            tooltip: {
                y: {
                    formatter: function (
                        value
                    ) {
                        return (
                            formatInteger(value) +
                            ' registros'
                        );
                    }
                }
            },

            responsive: [
                {
                    breakpoint: 650,
                    options: {
                        chart: {
                            height: 340
                        },

                        legend: {
                            position: 'bottom'
                        }
                    }
                }
            ]
        };

        chartInstances.debitStatus =
            new ApexCharts(
                elements.debitStatusChart,
                options
            );

        chartInstances.debitStatus.render();
    }


    function getStatusColor(status) {
        var normalized = normalizeText(
            status
        );

        var colors = {
            debito: '#d64550',
            nao_debito: '#249466',
            pendente: '#e2a227',
            sem_historico: '#7a8799'
        };

        return (
            colors[normalized] ||
            '#2f5edb'
        );
    }


    function destroyChart(name) {
        if (
            chartInstances[name] &&
            typeof chartInstances[name]
                .destroy === 'function'
        ) {
            chartInstances[name]
                .destroy();
        }

        chartInstances[name] = null;
    }


    // ==================================================
    // Rankings minimalistas
    // ==================================================

    function renderRankingList(
        container,
        rows,
        options
    ) {
        if (!container) {
            return;
        }

        options = options || {};

        var normalized = normalizeRows(
            rows,
            options.labelKeys,
            options.valueKeys
        )
            .sort(function (a, b) {
                return b.value - a.value;
            });

        if (options.maxItems) {
            normalized = normalized.slice(
                0,
                options.maxItems
            );
        }

        container.innerHTML = '';

        if (!normalized.length) {
            renderEmptyChart(container);
            return;
        }

        var maxValue = normalized.reduce(
            function (maximum, item) {
                return Math.max(
                    maximum,
                    item.value
                );
            },
            0
        );

        var list = document.createElement(
            'div'
        );

        list.className =
            'ranking-list__items';

        normalized.forEach(
            function (item) {
                var row = document.createElement(
                    'div'
                );

                row.className =
                    'ranking-item';

                var header = document.createElement(
                    'div'
                );

                header.className =
                    'ranking-item__header';

                var label =
                    document.createElement(
                        'span'
                    );

                label.className =
                    'ranking-item__label';

                label.textContent =
                    item.label ||
                    'Não informado';

                label.title =
                    item.label ||
                    'Não informado';

                var value =
                    document.createElement(
                        'strong'
                    );

                value.className =
                    'ranking-item__value';

                value.textContent =
                    formatInteger(
                        item.value
                    );

                var track =
                    document.createElement(
                        'div'
                    );

                track.className =
                    'ranking-item__track';

                var bar =
                    document.createElement(
                        'div'
                    );

                bar.className =
                    'ranking-item__bar';

                var percentage =
                    maxValue > 0
                        ? (
                            item.value /
                            maxValue
                        ) * 100
                        : 0;

                bar.style.width =
                    Math.max(
                        percentage,
                        1.5
                    ) + '%';

                header.appendChild(label);
                header.appendChild(value);

                track.appendChild(bar);

                row.appendChild(header);
                row.appendChild(track);

                list.appendChild(row);
            }
        );

        container.appendChild(list);
    }


    function renderEmptyChart(container) {
        container.innerHTML = '';

        var empty = document.createElement(
            'div'
        );

        empty.className =
            'chart-empty';

        empty.textContent =
            'Nenhum dado disponível para este período.';

        container.appendChild(empty);
    }


    function renderChartLibraryError(
        container
    ) {
        container.innerHTML = '';

        var error = document.createElement(
            'div'
        );

        error.className =
            'chart-empty';

        error.textContent =
            'A biblioteca de gráficos não foi carregada.';

        container.appendChild(error);
    }


    // ==================================================
    // Regras de débito
    // ==================================================

    function renderRules(rules) {
        if (!elements.rulesTableBody) {
            return;
        }

        elements.rulesTableBody.innerHTML = '';

        if (
            !Array.isArray(rules) ||
            !rules.length
        ) {
            elements.rulesTableBody
                .appendChild(
                    createEmptyRow(
                        7,
                        'Nenhuma regra disponível.'
                    )
                );

            setText(
                elements.rulesCounter,
                '0 regras'
            );

            return;
        }

        rules.forEach(function (rule) {
            var row = document.createElement(
                'tr'
            );

            appendTextCell(
                row,
                getFirstValue(
                    rule,
                    [
                        'regra',
                        'codigo',
                        'rule'
                    ]
                ) || '—',
                'table-code'
            );

            appendTextCell(
                row,
                getFirstValue(
                    rule,
                    [
                        'descricao',
                        'description'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                getFirstValue(
                    rule,
                    [
                        'acao_recomendada',
                        'acao',
                        'recommended_action'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                formatInteger(
                    getFirstValue(
                        rule,
                        [
                            'quantidade',
                            'total',
                            'count'
                        ]
                    )
                ),
                'table-number'
            );

            appendTextCell(
                row,
                formatPercentage(
                    getFirstValue(
                        rule,
                        [
                            'percentual',
                            'percentage'
                        ]
                    )
                ),
                'table-number'
            );

            appendTextCell(
                row,
                formatPercentage(
                    getFirstValue(
                        rule,
                        [
                            'percentual_acumulado',
                            'acumulado',
                            'cumulative_percentage'
                        ]
                    )
                ),
                'table-number'
            );

            appendTextCell(
                row,
                formatCurrency(
                    getFirstValue(
                        rule,
                        [
                            'valor_carga',
                            'valor_total_carga',
                            'value'
                        ]
                    )
                ),
                'table-number'
            );

            elements.rulesTableBody
                .appendChild(row);
        });

        setText(
            elements.rulesCounter,
            formatInteger(rules.length) +
            (
                rules.length === 1
                    ? ' regra'
                    : ' regras'
            )
        );
    }

    function formatOccurrence(record) {
        var code = getFirstValue(
            record,
            [
                'codigo_ocorrencia',
                'ocorrencia',
                'codigo'
            ]
        );

        var description = getFirstValue(
            record,
            [
                'descricao_ocorrencia',
                'descricao',
                'descricao_ocor'
            ]
        );

        code = String(code || '').trim();
        description = String(description || '').trim();

        if (code && description) {
            return code + ' - ' + description;
        }

        return code || description || '—';
    }


    // ==================================================
    // Registros de atenção
    // ==================================================

    function renderAttentionRecords(
        records
    ) {
        if (!elements.attentionTableBody) {
            return;
        }

        elements.attentionTableBody
            .innerHTML = '';

        if (
            !Array.isArray(records) ||
            !records.length
        ) {
            elements.attentionTableBody
                .appendChild(
                    createEmptyRow(
                        12,
                        'Nenhum registro exige atenção.'
                    )
                );

            setText(
                elements.attentionCounter,
                '0 registros'
            );

            return;
        }

        records.forEach(function (record) {
            var row = document.createElement(
                'tr'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'ctrc',
                        'numero_ctrc',
                        'conhecimento'
                    ]
                ) || '—',
                'table-code'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'nota_fiscal',
                        'nf',
                        'numero_nota'
                    ]
                ) || '—',
                'table-code'
            );

            appendTextCell(
                row,
                formatDate(
                    getFirstValue(
                        record,
                        [
                            'data_inclusao_ctrc',
                            'data_emissao_ctrc',
                            'emissao_ctrc'
                        ]
                    )
                )
            );

            appendTextCell(
                row,
                formatDate(
                    getFirstValue(
                        record,
                        [
                            'data_ocorrencia',
                            'data'
                        ]
                    )
                )
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'grupo_cliente',
                        'grupo'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'cliente',
                        'nome_cliente'
                    ]
                ) || '—',
                'table-long-text'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'unidade',
                        'filial'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                formatOccurrence(record),
                'table-occurrence'
            );

            appendBadgeCell(
                row,
                getFirstValue(
                    record,
                    [
                        'status_debito',
                        'status',
                        'status_validacao_debito'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'regra_debito',
                        'regra',
                        'regra_validacao_debito'
                    ]
                ) || '—'
            );

            appendTextCell(
                row,
                getFirstValue(
                    record,
                    [
                        'produto',
                        'produto_predominante'
                    ]
                ) || '—',
                'table-long-text'
            );

            appendTextCell(
                row,
                formatCurrency(
                    getFirstValue(
                        record,
                        [
                            'valor_carga',
                            'valor_total_carga'
                        ]
                    )
                ),
                'table-number'
            );

            elements.attentionTableBody
                .appendChild(row);
        });

        setText(
            elements.attentionCounter,
            formatInteger(records.length) +
            (
                records.length === 1
                    ? ' registro'
                    : ' registros'
            )
        );
    }


    function appendTextCell(
        row,
        value,
        className
    ) {
        var cell = document.createElement(
            'td'
        );

        if (className) {
            cell.className = className;
        }

        var finalValue =
            value === null ||
            typeof value === 'undefined' ||
            value === ''
                ? '—'
                : String(value);

        cell.textContent = finalValue;

        if (
            className === 'table-occurrence' ||
            className === 'table-long-text'
        ) {
            cell.title = finalValue;
        }

        row.appendChild(cell);
    }


    function appendBadgeCell(
        row,
        value
    ) {
        var cell = document.createElement(
            'td'
        );

        var badge = document.createElement(
            'span'
        );

        badge.className =
            'status-badge ' +
            getStatusBadgeClass(value);

        badge.textContent =
            formatStatus(value);

        cell.appendChild(badge);
        row.appendChild(cell);
    }


    function getStatusBadgeClass(value) {
        var status = normalizeText(value);

        if (status === 'debito') {
            return 'status-badge--danger';
        }

        if (status === 'nao_debito') {
            return 'status-badge--success';
        }

        if (status === 'pendente') {
            return 'status-badge--warning';
        }

        if (status === 'sem_historico') {
            return 'status-badge--neutral';
        }

        return 'status-badge--default';
    }


    function formatStatus(value) {
        var status = normalizeText(value);

        var labels = {
            debito: 'Débito',
            nao_debito: 'Não débito',
            pendente: 'Pendente',
            sem_historico: 'Sem histórico'
        };

        return (
            labels[status] ||
            String(value || '—')
        );
    }


    function createEmptyRow(
        colspan,
        message
    ) {
        var row = document.createElement(
            'tr'
        );

        var cell = document.createElement(
            'td'
        );

        cell.colSpan = colspan;
        cell.className = 'table-empty';
        cell.textContent = message;

        row.appendChild(cell);

        return row;
    }


    // ==================================================
    // Snapshot
    // ==================================================

    function updateSnapshotStatus(data) {
        if (
            !data ||
            data.available === false
        ) {
            setSnapshotUnavailable();
            return;
        }

        var snapshot =
            data.snapshot || {};

        setText(
            elements.snapshotName,
            snapshot.nome ||
            'Snapshot disponível'
        );

        if (elements.snapshotStatus) {
            elements.snapshotStatus
                .classList.remove(
                    'snapshot-status--unavailable'
                );

            elements.snapshotStatus
                .classList.add(
                    'snapshot-status--available'
                );
        }

        if (elements.snapshotIndicator) {
            elements.snapshotIndicator
                .setAttribute(
                    'title',
                    'Snapshot disponível'
                );
        }
    }


    function setSnapshotUnavailable() {
        setText(
            elements.snapshotName,
            'Snapshot indisponível'
        );

        if (elements.snapshotStatus) {
            elements.snapshotStatus
                .classList.remove(
                    'snapshot-status--available'
                );

            elements.snapshotStatus
                .classList.add(
                    'snapshot-status--unavailable'
                );
        }

        if (elements.snapshotIndicator) {
            elements.snapshotIndicator
                .setAttribute(
                    'title',
                    'Snapshot indisponível'
                );
        }
    }


    // ==================================================
    // Estados
    // ==================================================

    function setLoadingState() {
        setHidden(
            elements.loading,
            false
        );

        setHidden(
            elements.error,
            true
        );

        if (!dashboardData) {
            setHidden(
                elements.content,
                true
            );
        }

        if (elements.filtersForm) {
            elements.filtersForm
                .classList.add(
                    'is-loading'
                );
        }
    }


    function setContentState() {
        setHidden(
            elements.loading,
            true
        );

        setHidden(
            elements.error,
            true
        );

        setHidden(
            elements.content,
            false
        );

        if (elements.filtersForm) {
            elements.filtersForm
                .classList.remove(
                    'is-loading'
                );
        }
    }


    function setErrorState(message) {
        setHidden(
            elements.loading,
            true
        );

        setHidden(
            elements.error,
            false
        );

        if (!dashboardData) {
            setHidden(
                elements.content,
                true
            );
        }

        setText(
            elements.errorMessage,
            message ||
            'Verifique a disponibilidade dos dados.'
        );

        if (elements.filtersForm) {
            elements.filtersForm
                .classList.remove(
                    'is-loading'
                );
        }
    }


    function setHidden(
        element,
        hidden
    ) {
        if (!element) {
            return;
        }

        element.hidden = hidden;
    }


    // ==================================================
    // Toast e botões
    // ==================================================

    function showToast(
        message,
        type
    ) {
        if (
            !elements.toast ||
            !elements.toastMessage
        ) {
            return;
        }

        window.clearTimeout(
            toastTimer
        );

        elements.toast.className =
            'dashboard-toast dashboard-toast--' +
            (type || 'default');

        elements.toastMessage.textContent =
            message;

        elements.toast.hidden = false;

        toastTimer = window.setTimeout(
            function () {
                elements.toast.hidden = true;
            },
            4500
        );
    }


    function setButtonLoading(
        button,
        loading,
        loadingText
    ) {
        if (!button) {
            return;
        }

        if (loading) {
            button.setAttribute(
                'data-original-text',
                button.textContent
            );

            button.disabled = true;

            button.classList.add(
                'is-loading'
            );

            if (loadingText) {
                button.textContent =
                    loadingText;
            }

            return;
        }

        var originalText =
            button.getAttribute(
                'data-original-text'
            );

        if (originalText) {
            button.textContent =
                originalText;
        }

        button.disabled = false;

        button.classList.remove(
            'is-loading'
        );

        button.removeAttribute(
            'data-original-text'
        );
    }


    // ==================================================
    // Formatação
    // ==================================================

    function formatInteger(value) {
        return new Intl.NumberFormat(
            'pt-BR',
            {
                maximumFractionDigits: 0
            }
        ).format(
            parseNumber(value)
        );
    }


    function formatCurrency(value) {
        return new Intl.NumberFormat(
            'pt-BR',
            {
                style: 'currency',
                currency: 'BRL'
            }
        ).format(
            parseNumber(value)
        );
    }


    function formatPercentage(value) {
        return new Intl.NumberFormat(
            'pt-BR',
            {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            }
        ).format(
            parseNumber(value)
        ) + '%';
    }


    function formatDate(value) {
        if (!value) {
            return '—';
        }

        var date = parseDate(value);

        if (!date) {
            return String(value);
        }

        return new Intl.DateTimeFormat(
            'pt-BR',
            {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric'
            }
        ).format(date);
    }


    function formatDateTime(value) {
        if (!value) {
            return '—';
        }

        var date = parseDate(value);

        if (!date) {
            return String(value);
        }

        return new Intl.DateTimeFormat(
            'pt-BR',
            {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }
        ).format(date);
    }


    function formatShortChartLabel(value) {
        if (!value) {
            return '—';
        }

        var date = parseDate(value);

        if (date) {
            return new Intl.DateTimeFormat(
                'pt-BR',
                {
                    day: '2-digit',
                    month: '2-digit'
                }
            ).format(date);
        }

        var text = String(value);

        return text.length > 12
            ? text.substring(0, 12) + '…'
            : text;
    }


    function parseDate(value) {
        if (value instanceof Date) {
            return isNaN(
                value.getTime()
            )
                ? null
                : value;
        }

        var text =
            String(value).trim();

        if (!text) {
            return null;
        }

        var isoMatch = text.match(
            /^(\d{4})-(\d{2})-(\d{2})/
        );

        if (isoMatch) {
            return new Date(
                Number(isoMatch[1]),
                Number(isoMatch[2]) - 1,
                Number(isoMatch[3])
            );
        }

        var brazilianMatch = text.match(
            /^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2}))?$/
        );

        if (brazilianMatch) {
            return new Date(
                Number(brazilianMatch[3]),
                Number(brazilianMatch[2]) - 1,
                Number(brazilianMatch[1]),
                Number(
                    brazilianMatch[4] || 0
                ),
                Number(
                    brazilianMatch[5] || 0
                )
            );
        }

        var date = new Date(text);

        return isNaN(date.getTime())
            ? null
            : date;
    }


    function normalizeDateInputValue(value) {
        if (!value) {
            return '';
        }

        var text = String(value);

        if (
            /^\d{4}-\d{2}-\d{2}/
                .test(text)
        ) {
            return text.substring(
                0,
                10
            );
        }

        var date = parseDate(text);

        if (!date) {
            return '';
        }

        var year =
            date.getFullYear();

        var month = String(
            date.getMonth() + 1
        ).padStart(2, '0');

        var day = String(
            date.getDate()
        ).padStart(2, '0');

        return (
            year +
            '-' +
            month +
            '-' +
            day
        );
    }


    function parseNumber(value) {
        if (
            value === null ||
            typeof value === 'undefined' ||
            value === ''
        ) {
            return 0;
        }

        if (typeof value === 'number') {
            return isFinite(value)
                ? value
                : 0;
        }

        var text = String(value)
            .trim()
            .replace(/\s/g, '')
            .replace(/^R\$/i, '');

        if (
            text.indexOf(',') !== -1 &&
            text.indexOf('.') !== -1
        ) {
            text = text
                .replace(/\./g, '')
                .replace(',', '.');
        } else if (
            text.indexOf(',') !== -1
        ) {
            text = text.replace(
                ',',
                '.'
            );
        }

        var number = Number(text);

        return isFinite(number)
            ? number
            : 0;
    }


    function normalizeText(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(
                /[\u0300-\u036f]/g,
                ''
            )
            .trim()
            .toLowerCase()
            .replace(
                /[^a-z0-9]+/g,
                '_'
            )
            .replace(
                /^_+|_+$/g,
                ''
            );
    }


    // ==================================================
    // Utilitários
    // ==================================================

    function getFirstValue(
        object,
        keys
    ) {
        if (
            !object ||
            !Array.isArray(keys)
        ) {
            return null;
        }

        var index;

        for (
            index = 0;
            index < keys.length;
            index += 1
        ) {
            var key = keys[index];

            if (
                Object.prototype
                    .hasOwnProperty.call(
                        object,
                        key
                    ) &&
                object[key] !== null &&
                typeof object[key] !==
                    'undefined'
            ) {
                return object[key];
            }
        }

        return null;
    }


    function setText(
        element,
        value
    ) {
        if (!element) {
            return;
        }

        element.textContent =
            value === null ||
            typeof value === 'undefined'
                ? '—'
                : String(value);
    }


    initialize();
})();