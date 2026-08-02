django.jQuery(function($) {
    function toggleFields() {
        var role = $('select[id$="-role"]').val();
        var academiaRow = $('.field-academia');
        var departmentRow = $('.field-department');
        var expertiseRow = $('.field-expertise');

        academiaRow.hide();
        departmentRow.hide();
        expertiseRow.hide();

        if (role === 'researcher') {
            academiaRow.show();
            departmentRow.show();
        } else if (role === 'checker') {
            expertiseRow.show();
        }
        // public and admin: nothing extra shown
    }
    $(document).on('change', 'select[id$="-role"]', toggleFields);
    toggleFields();
});